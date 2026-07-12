"""In-memory trip state store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from schemas import AgentResult, Itinerary, TripPreferences, TripState

# Simple dict-backed store, keyed by trip_id
_trips: dict[str, TripState] = {}


def create_trip(prefs: TripPreferences) -> TripState:
    """Create a new trip and return its state."""
    trip_id = str(uuid.uuid4())
    state = TripState(
        trip_id=trip_id,
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
