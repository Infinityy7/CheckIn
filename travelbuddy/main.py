"""TravelBuddy — AI-powered travel planning API."""

from __future__ import annotations

import json
import logging
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import LOG_LEVEL
from itinerary import generate_itinerary
from orchestrator import generate_context_brief, run_agents_streaming
from schemas import (
    AgentResult,
    Itinerary,
    Recommendation,
    SelectionsInput,
    TripPreferences,
    TripState,
)
from store import (
    add_agent_result,
    add_research_error,
    create_trip,
    get_all_recommendations,
    get_trip,
    set_context_brief,
    set_itinerary,
    set_selections,
)

# --- Logging ---
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# --- App ---
app = FastAPI(
    title="TravelBuddy",
    description="AI-powered travel planning with specialized research agents",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request logging middleware ---

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every incoming request with method, path, and response time."""
    start = time.time()
    response = await call_next(request)
    elapsed_ms = (time.time() - start) * 1000
    logger.info(
        "%s %s → %d (%.0fms)",
        request.method, request.url.path, response.status_code, elapsed_ms,
    )
    return response


# --- Endpoints ---

@app.get("/api/health")
async def health() -> dict:
    """Basic health check."""
    return {"status": "ok"}


@app.post("/api/trip/preferences")
async def submit_preferences(prefs: TripPreferences) -> dict:
    """Accept trip preferences and create a new trip.

    Returns the trip_id used to reference this trip in all subsequent calls.
    """
    state = create_trip(prefs)
    logger.info("Trip created: %s → %s", state.trip_id, prefs.destination)
    return {
        "trip_id": state.trip_id,
        "status": "received",
        "preferences": prefs.model_dump(mode="json"),
    }


@app.post("/api/trip/{trip_id}/research")
async def research_trip(trip_id: str):
    """Run all 4 agents and stream results via Server-Sent Events.

    Each agent's results are streamed as soon as it finishes — the client
    does not have to wait for all 4 to complete.
    """
    state = get_trip(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")

    async def event_stream():
        # Generate context brief first
        context_brief = await generate_context_brief(state.preferences)
        set_context_brief(trip_id, context_brief)

        yield _sse({"event": "context_brief_generated", "brief": context_brief})

        # Stream agent results as each completes
        async for event in run_agents_streaming(state.preferences, context_brief):
            if event["event"] == "agent_completed":
                agent_result = AgentResult(
                    agent_name=event["agent"],
                    recommendations=[Recommendation(**r) for r in event["results"]],
                )
                add_agent_result(trip_id, agent_result)
            elif event["event"] == "agent_failed":
                add_research_error(trip_id, event["error"])

            yield _sse(event)

        yield _sse({"event": "all_complete", "trip_id": trip_id})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/trip/{trip_id}/select")
async def select_recommendations(trip_id: str, body: SelectionsInput) -> dict:
    """Save the user's selected recommendation IDs.

    Validates that every ID exists in the research results.
    """
    state = get_trip(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")

    if not state.research_results:
        raise HTTPException(status_code=400, detail="No research results yet — run /research first")

    # Validate IDs exist
    all_recs = get_all_recommendations(trip_id)
    valid_ids = {r.id for r in all_recs}
    invalid = [sid for sid in body.selections if sid not in valid_ids]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown recommendation IDs: {invalid}",
        )

    set_selections(trip_id, body.selections)
    logger.info("Trip %s: %d selections saved", trip_id, len(body.selections))
    return {"status": "selections_saved", "count": len(body.selections)}


@app.post("/api/trip/{trip_id}/itinerary")
async def generate_trip_itinerary(trip_id: str):
    """Generate a day-by-day itinerary from saved preferences and selections.

    Streams progress via Server-Sent Events.
    """
    state = get_trip(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")

    if not state.selections:
        raise HTTPException(status_code=400, detail="No selections saved — run /select first")

    if not state.research_results:
        raise HTTPException(status_code=400, detail="No research results — run /research first")

    # Resolve selected recommendations
    all_recs = get_all_recommendations(trip_id)
    selected_ids = set(state.selections)
    selected = [r for r in all_recs if r.id in selected_ids]

    if not selected:
        raise HTTPException(status_code=400, detail="Selected IDs did not match any recommendations")

    context_brief = state.context_brief or ""

    async def event_stream():
        yield _sse({"event": "itinerary_started", "trip_id": trip_id, "selection_count": len(selected)})

        try:
            itinerary = await generate_itinerary(state.preferences, context_brief, selected)
            set_itinerary(trip_id, itinerary)
            yield _sse({
                "event": "itinerary_complete",
                "itinerary": itinerary.model_dump(mode="json"),
            })
        except Exception as exc:
            logger.error("Itinerary generation failed: %s", exc, exc_info=True)
            yield _sse({"event": "itinerary_failed", "error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/trip/{trip_id}", response_model=TripState)
async def get_trip_state(trip_id: str) -> TripState:
    """Return the full current state of a trip.

    Includes preferences, research results, selections, and itinerary —
    whatever has been generated so far.
    """
    state = get_trip(trip_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")
    return state


# --- Helpers ---

def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
