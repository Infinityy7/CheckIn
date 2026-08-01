"""Durable trip state backed by the application database."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import db
from schemas import AgentResult, Itinerary, TripPreferences, TripState

RESEARCH_LEASE_MINUTES = 15


class ResearchAlreadyRunning(RuntimeError):
    """Raised when another healthy worker owns the research lease."""


class ResearchLeaseLost(RuntimeError):
    """Raised when a stale worker tries to write into a newer research run."""


def _save(state: TripState) -> None:
    """Persist a complete, validated trip snapshot.

    A JSON snapshot keeps the agent/itinerary payload flexible while the indexed
    ownership and timestamp fields live in normal database columns.
    """
    db.save_trip_state(state.model_dump(mode="json"))


def create_trip(prefs: TripPreferences, user_id: str = "") -> TripState:
    """Create a durable trip and return its state."""
    state = TripState(
        trip_id=str(uuid.uuid4()),
        user_id=user_id,
        preferences=prefs,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _save(state)
    return state


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
                "post_trip": None,
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
    db.mutate_trip_state(trip_id, lambda state: state.update({"selections": selections}))


def set_itinerary(trip_id: str, itinerary: Itinerary) -> None:
    payload = itinerary.model_dump(mode="json")
    db.mutate_trip_state(trip_id, lambda state: state.update({"itinerary": payload}))


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
