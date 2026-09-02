"""Durable trip state backed by the application database."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import db
from schemas import AgentResult, Itinerary, TripPreferences, TripState
from schemas import FeasibilityReport

RESEARCH_LEASE_MINUTES = 15
ITINERARY_LEASE_MINUTES = 10


class ResearchAlreadyRunning(RuntimeError):
    """Raised when another healthy worker owns the research lease."""


class ResearchLeaseLost(RuntimeError):
    """Raised when a stale worker tries to write into a newer research run."""


class ItineraryAlreadyRunning(RuntimeError):
    """Raised when another healthy worker is already generating this itinerary."""


class ItineraryLeaseLost(RuntimeError):
    """Raised when a stale worker tries to save an itinerary after its lease moved on."""


def _save(state: TripState) -> None:
    """Persist a complete, validated trip snapshot.

    A JSON snapshot keeps the agent/itinerary payload flexible while the indexed
    ownership and timestamp fields live in normal database columns.
    """
    db.save_trip_state(state.model_dump(mode="json"))


def create_trip(
    prefs: TripPreferences,
    user_id: str = "",
    idempotency_key: str | None = None,
    feasibility: FeasibilityReport | None = None,
) -> TripState:
    """Create a durable trip, or return the one already stored for this idempotency key.

    Two concurrent requests with the same key both reach the insert; the loser
    hits the unique index and is resolved to the winner's trip.
    """
    state = TripState(
        trip_id=str(uuid.uuid4()),
        user_id=user_id,
        preferences=prefs,
        idempotency_key=idempotency_key,
        feasibility=feasibility,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        _save(state)
    except db.TripIdempotencyConflict:
        existing = get_trip_by_idempotency_key(user_id, idempotency_key or "")
        if existing is None:
            raise
        return existing
    return state


def get_trip_by_idempotency_key(user_id: str, idempotency_key: str) -> TripState | None:
    """Return the trip a user already created under this key, if any."""
    if not idempotency_key:
        return None
    raw = db.find_trip_by_idempotency_key(user_id, idempotency_key)
    return None if raw is None else TripState.model_validate(raw)


def get_trip(trip_id: str) -> TripState | None:
    """Retrieve a trip by ID, or ``None`` when it does not exist."""
    raw = db.load_trip_state(trip_id)
    if raw is None:
        return None
    return TripState.model_validate(raw)


def list_user_trips(user_id: str) -> list[TripState]:
    """Return a user's trips newest first."""
    return [TripState.model_validate(raw) for raw in db.list_trip_states(user_id)]


def _require_lease(state: dict, lease_id: str) -> None:
    if state.get("research_lease_id") != lease_id:
        raise ResearchLeaseLost("This research run was replaced by a newer one")


def set_context_brief(trip_id: str, brief: str, lease_id: str) -> None:
    def update(state: dict) -> None:
        _require_lease(state, lease_id)
        state["context_brief"] = brief

    db.mutate_trip_state(trip_id, update)


def start_research(
    trip_id: str,
    *,
    preserve_results: bool = False,
    preserve_downstream: bool = False,
) -> str:
    """Acquire a research lease and prepare an idempotent run.

    ``preserve_results`` provides a last-known-good shortlist while refreshed
    categories arrive. ``preserve_downstream`` is only appropriate when a
    partial run is filling missing categories and existing IDs are unchanged.
    """
    now = datetime.now(timezone.utc)
    lease_id = str(uuid.uuid4())

    def acquire(state: dict) -> None:
        started_at = state.get("research_started_at")
        started = datetime.fromisoformat(started_at) if started_at else None
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        lease_is_live = (
            state.get("research_in_progress") is True
            and started is not None
            and started > now - timedelta(minutes=RESEARCH_LEASE_MINUTES)
        )
        if lease_is_live:
            raise ResearchAlreadyRunning("Research is already running for this trip")
        existing_results = list(state.get("research_results") or [])
        state.update({
            "research_in_progress": True,
            "research_started_at": now.isoformat(),
            "research_lease_id": lease_id,
            "research_results": existing_results if preserve_results else [],
            "research_errors": [],
        })
        if not preserve_downstream:
            # A full refreshed shortlist invalidates IDs from the previous run.
            state.update({
                "selections": None,
                "itinerary": None,
                "itinerary_fingerprint": None,
                "post_trip": None,
                "inventory_snapshots": {},
                "cart": None,
            })

    db.mutate_trip_state(trip_id, acquire)
    return lease_id


def finish_research(trip_id: str, lease_id: str) -> None:
    def release(state: dict) -> None:
        # A stale worker must not clear the newer worker's lease.
        if state.get("research_lease_id") != lease_id:
            return
        state.update({
            "research_in_progress": False,
            "research_started_at": None,
            "research_lease_id": None,
        })

    try:
        db.mutate_trip_state(trip_id, release)
    except KeyError:
        return


def add_agent_result(trip_id: str, result: AgentResult, lease_id: str) -> None:
    payload = result.model_dump(mode="json")

    def upsert(state: dict) -> None:
        _require_lease(state, lease_id)
        results = list(state.get("research_results") or [])
        for index, existing in enumerate(results):
            if existing.get("agent_name") == result.agent_name:
                results[index] = payload
                break
        else:
            results.append(payload)
        state["research_results"] = results

    db.mutate_trip_state(trip_id, upsert)


def add_research_error(trip_id: str, error: str, lease_id: str) -> None:
    def append(state: dict) -> None:
        _require_lease(state, lease_id)
        errors = list(state.get("research_errors") or [])
        errors.append(error)
        state["research_errors"] = errors

    db.mutate_trip_state(trip_id, append)


def set_selections(trip_id: str, selections: list[str]) -> None:
    """Persist the selected IDs; a changed set invalidates any stored itinerary."""
    ordered = list(dict.fromkeys(selections))

    def update(state: dict) -> None:
        if set(state.get("selections") or []) != set(ordered):
            state["itinerary"] = None
            state["itinerary_fingerprint"] = None
        state["selections"] = ordered

    db.mutate_trip_state(trip_id, update)


def cart_exact_choice_keys(state: dict) -> list[str]:
    """Sorted ``kind:recommendation_id:rate_plan_id`` keys for exact supplier picks."""
    cart = state.get("cart")
    items = cart.get("items") if isinstance(cart, dict) else None
    keys = {
        f"{item.get('kind')}:{item.get('recommendation_id')}:{item.get('rate_plan_id')}"
        for item in (items or [])
        if isinstance(item, dict)
        and item.get("kind") in {"hotel", "flight"}
        and item.get("rate_plan_id")
    }
    return sorted(keys)


def selection_fingerprint(state: dict) -> str:
    """Stable digest of the inputs an itinerary is built from."""
    payload = {
        "selections": sorted(set(state.get("selections") or [])),
        "exact_choices": cart_exact_choice_keys(state),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _lease_is_live(started_at: str | None, in_progress: object, minutes: int, now: datetime) -> bool:
    started = datetime.fromisoformat(started_at) if started_at else None
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return in_progress is True and started is not None and started > now - timedelta(minutes=minutes)


def start_itinerary(trip_id: str) -> str:
    """Acquire the itinerary lease so concurrent builds cannot double-spend."""
    now = datetime.now(timezone.utc)
    lease_id = str(uuid.uuid4())

    def acquire(state: dict) -> None:
        if _lease_is_live(
            state.get("itinerary_started_at"),
            state.get("itinerary_in_progress"),
            ITINERARY_LEASE_MINUTES,
            now,
        ):
            raise ItineraryAlreadyRunning("An itinerary is already being generated for this trip")
        state.update({
            "itinerary_in_progress": True,
            "itinerary_started_at": now.isoformat(),
            "itinerary_lease_id": lease_id,
        })

    db.mutate_trip_state(trip_id, acquire)
    return lease_id


def finish_itinerary(trip_id: str, lease_id: str) -> None:
    def release(state: dict) -> None:
        if state.get("itinerary_lease_id") != lease_id:
            return
        state.update({
            "itinerary_in_progress": False,
            "itinerary_started_at": None,
            "itinerary_lease_id": None,
        })

    try:
        db.mutate_trip_state(trip_id, release)
    except KeyError:
        return


def set_itinerary(trip_id: str, itinerary: Itinerary, lease_id: str, fingerprint: str) -> None:
    payload = itinerary.model_dump(mode="json")

    def update(state: dict) -> None:
        if state.get("itinerary_lease_id") != lease_id:
            raise ItineraryLeaseLost("This itinerary run was replaced by a newer one")
        state["itinerary"] = payload
        state["itinerary_fingerprint"] = fingerprint

    db.mutate_trip_state(trip_id, update)


def set_post_trip(trip_id: str, post_trip: dict) -> None:
    """Persist derived feedback state without overwriting concurrent trip fields."""
    db.mutate_trip_state(trip_id, lambda state: state.update({"post_trip": post_trip}))


def get_all_recommendations(trip_id: str) -> list:
    state = _required(trip_id)
    if not state.research_results:
        return []
    return [recommendation for result in state.research_results for recommendation in result.recommendations]


def _required(trip_id: str) -> TripState:
    state = get_trip(trip_id)
    if state is None:
        raise KeyError(f"Unknown trip {trip_id}")
    return state
