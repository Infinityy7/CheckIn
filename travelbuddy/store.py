"""In-memory trip state store."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from config import TRIP_TTL_HOURS
from schemas import AgentResult, Itinerary, TripPreferences, TripState

# Simple dict-backed store, keyed by trip_id
_trips: dict[str, TripState] = {}


def _purge_old_trips() -> None:
    # toss old trips so the dict doesn't grow forever
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TRIP_TTL_HOURS)
    expired = []
    for trip_id, state in _trips.items():
        created = datetime.fromisoformat(state.created_at)
        if created < cutoff:
            expired.append(trip_id)
    for trip_id in expired:
        del _trips[trip_id]


def create_trip(prefs: TripPreferences, user_id: str = "") -> TripState:
    """Create a new trip and return its state."""
    _purge_old_trips()
    trip_id = str(uuid.uuid4())
    state = TripState(
        trip_id=trip_id,
        user_id=user_id,
        preferences=prefs,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _trips[trip_id] = state
    return state


def get_trip(trip_id: str) -> TripState | None:
    """Retrieve a trip by ID, or None if not found."""
    return _trips.get(trip_id)


def set_context_brief(trip_id: str, brief: str) -> None:
    """Store the generated context brief."""
    _trips[trip_id].context_brief = brief


def start_research(trip_id: str) -> None:
    # mark research as running and wipe old results,
    # so re-running /research replaces instead of duplicating
    state = _trips[trip_id]
    state.research_in_progress = True
    state.research_results = []
    state.research_errors = []


def finish_research(trip_id: str) -> None:
    state = _trips.get(trip_id)
    if state is not None:
        state.research_in_progress = False


def add_agent_result(trip_id: str, result: AgentResult) -> None:
    """Append a single agent's result to the trip."""
    state = _trips[trip_id]
    if state.research_results is None:
        state.research_results = []
    state.research_results.append(result)


def add_research_error(trip_id: str, error: str) -> None:
    """Record an agent failure."""
    state = _trips[trip_id]
    if state.research_errors is None:
        state.research_errors = []
    state.research_errors.append(error)


def set_selections(trip_id: str, selections: list[str]) -> None:
    """Store the user's selected recommendation IDs."""
    _trips[trip_id].selections = selections


def set_itinerary(trip_id: str, itinerary: Itinerary) -> None:
    """Store the generated itinerary."""
    _trips[trip_id].itinerary = itinerary


def get_all_recommendations(trip_id: str) -> list:
    """Flatten all recommendations from all agent results."""
    state = _trips[trip_id]
    if not state.research_results:
        return []
    recs = []
    for ar in state.research_results:
        recs.extend(ar.recommendations)
    return recs
