"""TravelBuddy — AI-powered travel planning API."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

import auth
import api_errors
import db
import profiles
from config import ALLOWED_ORIGINS, LOG_LEVEL
from itinerary import generate_itinerary
from orchestrator import generate_context_brief, run_agents_streaming
from schemas import (
    AgentResult,
    CharacterProfileUpdate,
    ChatInput,
    Itinerary,
    LoginInput,
    RecommendationFeedbackInput,
    Recommendation,
    RegisterInput,
    SelectionsInput,
    TripPreferences,
    TripState,
)
from store import (
    add_agent_result,
    add_research_error,
    create_trip,
    finish_research,
    get_all_recommendations,
    get_trip,
    set_context_brief,
    set_itinerary,
    set_selections,
    start_research,
)

# --- Logging ---
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# create tables + one-time migration of the old users.json / .md files
db.init_db()

# --- App ---
app = FastAPI(
    title="TravelBuddy",
    description="AI-powered travel planning with specialized research agents",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[api_errors.REQUEST_ID_HEADER],
)

app.add_exception_handler(HTTPException, api_errors.http_exception_handler)
app.add_exception_handler(RequestValidationError, api_errors.validation_exception_handler)
app.add_exception_handler(Exception, api_errors.unexpected_exception_handler)


# --- Request logging middleware ---

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Attach a support-friendly request ID and log timing without user secrets."""
    request.state.request_id = api_errors.request_id_for(request)
    start = time.time()
    response = None
    try:
        response = await call_next(request)
        response.headers[api_errors.REQUEST_ID_HEADER] = request.state.request_id
        return response
    finally:
        elapsed_ms = (time.time() - start) * 1000
        status = response.status_code if response is not None else 500
        logger.info(
            "[%s] %s %s → %d (%.0fms)",
            request.state.request_id,
            request.method,
            request.url.path,
            status,
            elapsed_ms,
        )


# --- Helpers ---

def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


def _get_owned_trip(trip_id: str, user_id: str) -> TripState:
    # 404 for both missing and someone else's trip, so trip ids can't be probed
    state = get_trip(trip_id)
    if state is None or state.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")
    return state


# --- Auth endpoints ---

@app.get("/api/health")
async def health() -> dict:
    """Basic health check."""
    return {"status": "ok"}


@app.post("/api/auth/register")
async def register(body: RegisterInput) -> dict:
    """Create an account and log straight in."""
    token = auth.register(body.email, body.password)
    return {"token": token}


@app.post("/api/auth/login")
async def login(body: LoginInput) -> dict:
    """Log in, get a session token."""
    token = auth.login(body.email, body.password)
    return {"token": token}


@app.post("/api/auth/logout")
async def logout(token: str = Depends(auth.get_token)) -> dict:
    auth.logout(token)
    return {"status": "logged_out"}


@app.get("/api/auth/me")
async def me(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Who am I, have I done the intake chat, who do I travel with."""
    return {
        "email": auth.get_email(user_id),
        "intake_complete": profiles.load_sketch(user_id) is not None,
        "cotravellers": profiles.list_cotravellers(user_id),
    }


# --- Profile endpoints ---

@app.post("/api/profile/chat")
async def profile_chat(body: ChatInput, user_id: str = Depends(auth.get_current_user)) -> dict:
    """One turn of the intake conversation with Buddy.

    Send an empty message to start. When done=true, the sketch is saved.
    Set cotraveller_name to build a co-traveller's sketch instead.
    """
    reply, done = await profiles.chat_turn(user_id, body.message, body.cotraveller_name)
    return {"reply": reply, "done": done}


@app.get("/api/profile")
async def get_profile(user_id: str = Depends(auth.get_current_user)) -> dict:
    """The current character sketch plus saved co-travellers."""
    return {
        "sketch": profiles.load_sketch(user_id),
        "cotravellers": profiles.list_cotravellers(user_id),
    }


@app.get("/api/profile/character")
async def get_character_profile(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Stable typed contract for the persistent travel personality profile."""
    profile = profiles.get_character_profile(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Character profile not created yet")
    return profile


@app.put("/api/profile/character")
async def update_character_profile(
    body: CharacterProfileUpdate,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Edit the profile summary and structured traits."""
    if profiles.load_sketch(user_id) is None:
        raise HTTPException(status_code=404, detail="Character profile not created yet")
    return profiles.update_character_profile(user_id, body.summary, body.traits)


@app.post("/api/profile/character/reset")
async def reset_character_profile(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Delete the saved profile so onboarding can be retaken."""
    profiles.reset_character_profile(user_id)
    return {"status": "reset", "intake_complete": False}


@app.post("/api/profile/character/feedback")
async def character_feedback(
    body: RecommendationFeedbackInput,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Record a like/dislike signal for the next recommendation ranking run."""
    if profiles.load_sketch(user_id) is None:
        raise HTTPException(status_code=404, detail="Character profile not created yet")
    return profiles.apply_recommendation_feedback(
        user_id, body.recommendation_name, body.category, body.sentiment
    )


# --- Trip endpoints ---

@app.post("/api/trip/preferences")
async def submit_preferences(
    prefs: TripPreferences,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Accept trip preferences and create a new trip.

    Returns the trip_id used to reference this trip in all subsequent calls.
    """
    # intake chat is required once before planning
    if profiles.load_sketch(user_id) is None:
        raise HTTPException(status_code=403, detail="Finish the intake chat first")

    # only bring co-travellers that actually have sketches
    for name in prefs.cotravellers:
        if profiles.load_cotraveller(user_id, name) is None:
            raise HTTPException(status_code=400, detail=f"No saved co-traveller named '{name}'")

    state = create_trip(prefs, user_id)
    logger.info("Trip created: %s → %s", state.trip_id, prefs.destination)
    return {
        "trip_id": state.trip_id,
        "status": "received",
        "preferences": prefs.model_dump(mode="json"),
    }


@app.post("/api/trip/{trip_id}/research")
async def research_trip(
    trip_id: str,
    request: Request,
    user_id: str = Depends(auth.get_current_user),
):
    """Run all 4 agents and stream results via Server-Sent Events.

    Each agent's results are streamed as soon as it finishes — the client
    does not have to wait for all 4 to complete.
    """
    state = _get_owned_trip(trip_id, user_id)

    if state.research_in_progress:
        raise HTTPException(status_code=409, detail="Research is already running for this trip")

    # wipes old results so a re-run doesn't duplicate them
    start_research(trip_id)

    # taste profile: prose goes into the brief (light aim for the web
    # search), taste vectors go into the ranker (the heavy lifting)
    raw_sketch = profiles.load_sketch(user_id)
    brief_sketch = None
    user_taste = None
    if raw_sketch:
        brief_sketch, _ = profiles.parse_taste(raw_sketch)  # prose only, no json block
        user_taste = profiles.get_taste(user_id)

    cotraveller_sketches = []
    cotraveller_tastes = []
    for name in state.preferences.cotravellers:
        cot_raw = profiles.load_cotraveller(user_id, name)
        if cot_raw:
            cot_prose, _ = profiles.parse_taste(cot_raw)
            cotraveller_sketches.append(cot_prose)
        cot_taste = profiles.get_cotraveller_taste(user_id, name)
        if cot_taste:
            cotraveller_tastes.append(cot_taste)

    async def event_stream():
        try:
            # Generate context brief first
            context_brief = await generate_context_brief(
                state.preferences, brief_sketch, cotraveller_sketches
            )
            set_context_brief(trip_id, context_brief)

            yield _sse({"event": "context_brief_generated", "brief": context_brief})

            # Stream agent results as each completes
            async for event in run_agents_streaming(
                state.preferences, context_brief, user_taste, cotraveller_tastes
            ):
                if event["event"] == "agent_completed":
                    agent_result = AgentResult(
                        agent_name=event["agent"],
                        recommendations=[Recommendation(**r) for r in event["results"]],
                    )
                    add_agent_result(trip_id, agent_result)
                elif event["event"] == "agent_failed":
                    add_research_error(trip_id, event["error"])
                elif event["event"] == "all_complete":
                    event["trip_id"] = trip_id  # orchestrator already sends this one

                yield _sse(event)
        except Exception as exc:
            # response already started, so send an error event instead of just dying
            logger.error(
                "[%s] Research stream failed: %s",
                request.state.request_id,
                exc,
                exc_info=True,
            )
            yield _sse(api_errors.stream_problem(
                request,
                event="error",
                code="RESEARCH_INTERRUPTED",
                message=(
                    "Research stopped before every category finished. "
                    "Any completed results are still available, and you can retry safely."
                ),
            ))
        finally:
            finish_research(trip_id)

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
async def select_recommendations(
    trip_id: str,
    body: SelectionsInput,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Save the user's selected recommendation IDs.

    Validates that every ID exists in the research results.
    """
    state = _get_owned_trip(trip_id, user_id)

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
async def generate_trip_itinerary(
    trip_id: str,
    request: Request,
    user_id: str = Depends(auth.get_current_user),
):
    """Generate a day-by-day itinerary from saved preferences and selections.

    Streams progress via Server-Sent Events.
    """
    state = _get_owned_trip(trip_id, user_id)

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

            # learn from this trip in the background: what they picked vs skipped
            picked = [r.name for r in selected]
            skipped = [r.name for r in all_recs if r.id not in selected_ids]
            asyncio.create_task(
                profiles.update_sketch_from_trip(user_id, state.preferences, picked, skipped)
            )
        except Exception as exc:
            logger.error(
                "[%s] Itinerary generation failed: %s",
                request.state.request_id,
                exc,
                exc_info=True,
            )
            yield _sse(api_errors.stream_problem(
                request,
                event="itinerary_failed",
                code="ITINERARY_FAILED",
                message=(
                    "The itinerary could not be finished. Your selections are saved, "
                    "so it is safe to try again."
                ),
            ))

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
async def get_trip_state(trip_id: str, user_id: str = Depends(auth.get_current_user)) -> TripState:
    """Return the full current state of a trip.

    Includes preferences, research results, selections, and itinerary —
    whatever has been generated so far.
    """
    return _get_owned_trip(trip_id, user_id)


# serve the frontend, mounted last so /api routes win
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
