"""Advisory sanity check on trip requests, with a suggested fix.

A hopeless request (a $100 budget for ten days abroad) makes every research
agent flail: candidates fail the ranker's hard budget constraints and the
whole 16k-token research call retries before failing anyway. This check runs
once at trip creation and returns a verdict plus the smallest change that
would make the request workable. It is advisory and fail-open: it never
blocks creation, and any error degrades to verdict "unchecked".
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta

from config import FEASIBILITY_CHECK_ENABLED, FEASIBILITY_TIMEOUT_SECONDS
from llm_client import generate_text, parse_json_text
from prompts.feasibility import SYSTEM_PROMPT, build_user_prompt
from schemas import (
    CURRENCY_TO_USD,
    MAX_TRIP_DAYS,
    FeasibilityReport,
    SuggestedChanges,
    TripPreferences,
    planning_scope_note,
)

logger = logging.getLogger(__name__)

# Below this many USD per person per day the trip cannot work anywhere, so no
# model call is needed. Kept extreme on purpose: shoestring travel is a real
# market and nuanced judgment belongs to the LLM tier.
FLOOR_USD_PER_PERSON_DAY = 10.0
# Route-blind lodging+food floor used only to size the deterministic
# suggestion; the wording marks it as "at least" (transport comes on top).
SUGGESTED_USD_PER_PERSON_DAY = 40.0

def _unchecked() -> FeasibilityReport:
    return FeasibilityReport(verdict="unchecked")


def _trip_days(prefs: TripPreferences) -> int:
    return (prefs.end_date - prefs.start_date).days + 1


def _floor_check(prefs: TripPreferences) -> FeasibilityReport | None:
    if not {"hotel", "restaurant"}.intersection(prefs.scope):
        return None
    rate = CURRENCY_TO_USD.get(prefs.currency)
    if not rate:
        return None
    days = _trip_days(prefs)
    per_person_day = (prefs.budget_amount * rate) / (prefs.num_travelers * days)
    if per_person_day >= FLOOR_USD_PER_PERSON_DAY:
        return None
    suggested = round(
        SUGGESTED_USD_PER_PERSON_DAY * prefs.num_travelers * days / rate
    )
    return FeasibilityReport(
        verdict="unrealistic",
        confidence=0.95,
        reason=(
            f"That budget works out to about ${per_person_day:.0f} per person "
            f"per day, which cannot cover lodging and food anywhere, let alone "
            f"getting to {prefs.destination}."
        ),
        suggestion_text=(
            f"Try at least {suggested:g} {prefs.currency} for "
            f"{prefs.num_travelers} traveler(s) over {days} days before "
            "transport, or plan fewer days."
        ),
        suggested_changes=SuggestedChanges(budget_amount=float(suggested)),
    )


def _sanitize(data: dict, prefs: TripPreferences) -> FeasibilityReport:
    """Fold untrusted model output into a valid report, dropping bad parts."""
    verdict = data.get("verdict")
    if verdict not in {"ok", "tight", "unrealistic"}:
        return _unchecked()

    raw_confidence = data.get("confidence")
    confidence = raw_confidence if isinstance(raw_confidence, (int, float)) else 0
    confidence = min(1.0, max(0.0, float(confidence)))

    changes = SuggestedChanges()
    raw_changes = data.get("suggested_changes")
    if verdict != "ok" and isinstance(raw_changes, dict):
        budget = raw_changes.get("budget_amount")
        if (
            isinstance(budget, (int, float))
            and not isinstance(budget, bool)
            and math.isfinite(budget)
            and budget > 0
        ):
            changes.budget_amount = float(budget)
        try:
            candidate = SuggestedChanges(
                end_date=raw_changes.get("end_date") or None
            ).end_date
        except ValueError:
            candidate = None
        if candidate is not None:
            latest = prefs.start_date + timedelta(days=MAX_TRIP_DAYS)
            if prefs.start_date <= candidate <= latest:
                changes.end_date = candidate
        destination = raw_changes.get("destination")
        if isinstance(destination, str) and destination.strip():
            changes.destination = destination.strip()

    return FeasibilityReport(
        verdict=verdict,
        confidence=confidence,
        reason=str(data.get("reason") or "") if verdict != "ok" else "",
        suggestion_text=str(data.get("suggestion_text") or "") if verdict != "ok" else "",
        suggested_changes=changes,
    )


async def check_feasibility(prefs: TripPreferences) -> FeasibilityReport:
    """Return an advisory feasibility verdict for a trip request."""
    if not FEASIBILITY_CHECK_ENABLED:
        return _unchecked()

    floor = _floor_check(prefs)
    if floor is not None:
        return floor

    try:
        async with asyncio.timeout(FEASIBILITY_TIMEOUT_SECONDS):
            raw_text = await generate_text(
                build_user_prompt(
                    prefs.model_dump_json(indent=2), planning_scope_note(prefs.scope)
                ),
                system_instruction=SYSTEM_PROMPT,
                max_tokens=1000,
                cheap=True,
                json_mode=True,
                effort="low",
                operation="feasibility",
            )
        data = parse_json_text(raw_text)
        if not isinstance(data, dict):
            raise ValueError("feasibility response was not a JSON object")
        report = _sanitize(data, prefs)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Feasibility check unavailable; trip proceeds unchecked error=%s",
            type(exc).__name__,
        )
        return _unchecked()
    if report.verdict == "unchecked":
        logger.warning("Feasibility check returned an unusable verdict; ignoring it")
    return report
