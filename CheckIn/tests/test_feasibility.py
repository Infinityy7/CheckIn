"""Advisory feasibility check: deterministic floor, sanitization, fail-open."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

import feasibility
from schemas import GroupType, TripPreferences


def _prefs(**overrides) -> TripPreferences:
    values = dict(
        destination="Tokyo",
        origin="Mumbai",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
        budget_amount=4200,
        currency="USD",
        vibes=["culture"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )
    values.update(overrides)
    return TripPreferences(**values)


def _stub_llm(monkeypatch, payload: dict) -> list[dict]:
    calls: list[dict] = []

    async def fake(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return json.dumps(payload)

    monkeypatch.setattr(feasibility, "generate_text", fake)
    return calls


def test_absurd_budget_fails_the_floor_without_a_model_call():
    # $100 for 2 people over 7 days ≈ $7/person/day; conftest raises on any
    # LLM call, so reaching a verdict proves the deterministic path was used.
    report = asyncio.run(feasibility.check_feasibility(_prefs(budget_amount=100)))

    assert report.verdict == "unrealistic"
    assert report.confidence >= 0.9
    assert report.suggested_changes.budget_amount and report.suggested_changes.budget_amount > 100
    assert report.suggestion_text
    assert "Tokyo" in report.reason


def test_ok_verdict_clears_any_stray_suggestions(monkeypatch):
    _stub_llm(monkeypatch, {
        "verdict": "ok",
        "confidence": 0.9,
        "reason": "should be dropped",
        "suggestion_text": "should be dropped",
        "suggested_changes": {"budget_amount": 9999},
    })
    report = asyncio.run(feasibility.check_feasibility(_prefs()))

    assert report.verdict == "ok"
    assert report.reason == ""
    assert report.suggestion_text == ""
    assert report.suggested_changes.budget_amount is None


def test_tight_verdict_keeps_valid_suggestions(monkeypatch):
    calls = _stub_llm(monkeypatch, {
        "verdict": "tight",
        "confidence": 0.7,
        "reason": "Flights eat most of this budget.",
        "suggestion_text": "Raise the budget to about 5200 USD or drop a day.",
        "suggested_changes": {
            "budget_amount": 5200,
            "end_date": "2026-10-16",
            "destination": None,
        },
    })
    report = asyncio.run(feasibility.check_feasibility(_prefs()))

    assert report.verdict == "tight"
    assert report.suggested_changes.budget_amount == 5200
    assert report.suggested_changes.end_date == date(2026, 10, 16)
    assert report.suggested_changes.destination is None
    assert calls and calls[0]["cheap"] is True and calls[0]["json_mode"] is True


def test_garbage_model_output_is_sanitized(monkeypatch):
    _stub_llm(monkeypatch, {
        "verdict": "unrealistic",
        "confidence": 7,
        "reason": 42,
        "suggestion_text": None,
        "suggested_changes": {
            "budget_amount": -5,
            "end_date": "2026-10-01",  # before start_date
            "destination": "   ",
        },
    })
    report = asyncio.run(feasibility.check_feasibility(_prefs()))

    assert report.verdict == "unrealistic"
    assert report.confidence == 1.0
    assert report.suggested_changes.budget_amount is None
    assert report.suggested_changes.end_date is None
    assert report.suggested_changes.destination is None


def test_unknown_verdict_degrades_to_unchecked(monkeypatch):
    _stub_llm(monkeypatch, {"verdict": "stupid", "confidence": 1})
    report = asyncio.run(feasibility.check_feasibility(_prefs()))
    assert report.verdict == "unchecked"


def test_model_failure_is_fail_open():
    # conftest's autouse stub raises on any LLM call
    report = asyncio.run(feasibility.check_feasibility(_prefs()))
    assert report.verdict == "unchecked"
    assert report.confidence == 0


def test_kill_switch_skips_everything(monkeypatch):
    monkeypatch.setattr(feasibility, "FEASIBILITY_CHECK_ENABLED", False)
    report = asyncio.run(feasibility.check_feasibility(_prefs(budget_amount=100)))
    assert report.verdict == "unchecked"


def test_slow_model_fails_open_inside_the_configured_deadline(monkeypatch):
    deadline = 0.2
    monkeypatch.setattr(feasibility, "FEASIBILITY_TIMEOUT_SECONDS", deadline)

    async def hanging_model(*_args, **_kwargs):
        await asyncio.sleep(5)
        return '{"verdict": "ok"}'

    monkeypatch.setattr(feasibility, "generate_text", hanging_model)
    started = time.perf_counter()
    report = asyncio.run(feasibility.check_feasibility(_prefs()))
    elapsed = time.perf_counter() - started

    assert report.verdict == "unchecked"
    assert elapsed < deadline + 0.5
