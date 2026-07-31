"""TravelBuddy — AI-powered travel planning API."""

from __future__ import annotations

import json
import logging
import time

from datetime import date, timedelta
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
from personalization import (
    MAX_TAG_BATCH_DELTA,
    apply_weight_adjustments,
    learn_from_rating,
    learn_from_selections,
)
from schemas import (
    AgentResult,
    CharacterProfileUpdate,
    ChatInput,
    IntakeAnswerInput,
    Itinerary,
    LoginInput,
    PostTripFeedbackInput,
    PostTripState,
    RecommendationFeedbackInput,
    Recommendation,
    RegisterInput,
    SelectionsInput,
    TripPreferences,
    TripState,
)
from store import (
    ResearchAlreadyRunning,
    add_agent_result,
    add_research_error,
    create_trip,
    finish_research,
    get_all_recommendations,
    get_trip,
    list_user_trips,
    set_context_brief,
    set_itinerary,
    set_post_trip,
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


def _selected_recommendations(state: TripState) -> list[Recommendation]:
    selected_ids = set(state.selections or [])
    if not selected_ids or not state.research_results:
        return []
    return [
        recommendation
        for result in state.research_results
        for recommendation in result.recommendations
        if recommendation.id in selected_ids
    ]


def _adjustment_rows(before: dict, after: dict, deltas: dict[str, float]) -> list[dict]:
    before_vibes = before.get("vibe_weights", {})
    after_vibes = after.get("vibe_weights", {})
    return [
        {
            "key": key,
            "before": round(float(before_vibes.get(key, 0.0)), 4),
            "after": round(float(after_vibes.get(key, 0.0)), 4),
            "delta": round(float(after_vibes.get(key, 0.0)) - float(before_vibes.get(key, 0.0)), 4),
        }
        for key in deltas
    ]


def _post_trip_state(state: TripState, user_id: str) -> PostTripState:
    """Derive rating eligibility and saved feedback from server-side state."""
    feedback = db.get_trip_feedback(state.trip_id, user_id)
    eligible = state.itinerary is not None and state.preferences.end_date < date.today()
    details = feedback.get("details", {}) if feedback else {}
    return PostTripState(
        eligible=eligible or feedback is not None,
        eligibleAt=(state.preferences.end_date + timedelta(days=1)).isoformat(),
        rating=feedback.get("rating") if feedback else None,
        submittedAt=feedback.get("created_at") if feedback else None,
        adjustments=details.get("adjustments", []) if isinstance(details, dict) else [],
    )


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


@app.get("/api/profile/intake")
async def get_profile_intake(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Return the durable nine-question onboarding draft and next question."""
    return profiles.get_intake_state(user_id)


@app.put("/api/profile/intake/answers/{question_id}")
async def save_profile_intake_answer(
    question_id: str,
    body: IntakeAnswerInput,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Validate and persist one onboarding answer."""
    try:
        return profiles.save_intake_answer(user_id, question_id, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/profile/intake/complete")
async def complete_profile_intake(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Generate character.md and compile the ranker's structured weights."""
    try:
        return await profiles.complete_intake(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/profile/intake")
async def delete_profile_intake(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Clear the saved draft/profile so the questionnaire can be retaken."""
    profiles.reset_intake(user_id)
    return {"status": "reset", "intake_complete": False}


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
    try:
        return profiles.update_character_profile(
            user_id,
            body.summary,
            weights=body.weights,
            traits=body.traits,
            expected_version=body.expected_version,
        )
    except db.ProfileVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="Your profile changed elsewhere. Refresh it before saving again.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    state = _get_owned_trip(body.trip_id, user_id)
    recommendation = next(
        (item for item in get_all_recommendations(state.trip_id) if item.id == body.recommendation_id),
        None,
    )
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found for this trip")
    weights = profiles.get_taste(user_id) or {}
    positive = learn_from_selections(weights, [recommendation])
    deltas = positive if body.sentiment == "like" else {key: -value for key, value in positive.items()}
    if deltas:
        db.apply_preference_learning(
            user_id,
            state.trip_id,
            f"explicit_{body.sentiment}",
            f"feedback:{state.trip_id}:{recommendation.id}:{body.sentiment}:v1",
            deltas,
            payload={
                "recommendation_id": recommendation.id,
                "category": recommendation.category,
                "signal_value": 1 if body.sentiment == "like" else -1,
            },
        )
    return profiles.get_character_profile(user_id) or {}


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
    try:
        # Atomically acquires a renewable-enough lease and clears stale output.
        research_lease_id = start_research(trip_id)
    except ResearchAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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
            set_context_brief(trip_id, context_brief, research_lease_id)

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
                    add_agent_result(trip_id, agent_result, research_lease_id)
                elif event["event"] == "agent_failed":
                    add_research_error(trip_id, event["error"], research_lease_id)
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
            finish_research(trip_id, research_lease_id)

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


@app.get("/api/trips/pending-check-in")
async def pending_trip_check_in(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Return the newest finished, unrated trip that is ready for feedback."""
    for state in list_user_trips(user_id):
        post_trip = _post_trip_state(state, user_id)
        if post_trip.eligible and post_trip.rating is None:
            return {
                "trip": {
                    "trip_id": state.trip_id,
                    "destination": state.preferences.destination,
                    "end_date": state.preferences.end_date.isoformat(),
                    "trip_title": (
                        state.itinerary.trip_title
                        if state.itinerary is not None
                        else f"{state.preferences.destination} trip"
                    ),
                }
            }
    return {"trip": None}


@app.put("/api/trip/{trip_id}/post-trip-feedback")
async def save_post_trip_feedback(
    trip_id: str,
    body: PostTripFeedbackInput,
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """Save one replay-safe 1–5 rating and gently tune selected vibe weights."""
    state = _get_owned_trip(trip_id, user_id)
    post_trip = _post_trip_state(state, user_id)
    if not post_trip.eligible:
        raise HTTPException(status_code=409, detail="This trip is not ready for a post-trip check-in")

    selected = _selected_recommendations(state)
    before = profiles.get_taste(user_id) or {}
    selection_deltas = learn_from_selections(before, selected)
    rating_deltas = learn_from_rating(before, selected, body.overall_rating)
    deltas = {
        key: max(
            -MAX_TAG_BATCH_DELTA,
            min(MAX_TAG_BATCH_DELTA, selection_deltas.get(key, 0.0) + rating_deltas.get(key, 0.0)),
        )
        for key in set(selection_deltas) | set(rating_deltas)
    }
    projected = apply_weight_adjustments(before, deltas)
    adjustments = _adjustment_rows(before, projected, deltas)
    try:
        result = db.apply_preference_learning(
            user_id,
            trip_id,
            "post_trip_rating",
            f"post-trip:{trip_id}:v1",
            deltas,
            rating=body.overall_rating,
            payload={
                "adjustments": adjustments,
                "recommendation_ids": [item.id for item in selected],
                "selection_deltas": selection_deltas,
                "rating_deltas": rating_deltas,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    feedback = result.get("feedback") or db.get_trip_feedback(trip_id, user_id) or {}
    persisted_weights = (result.get("profile") or {}).get("weights") or projected
    feedback_details = feedback.get("details") if isinstance(feedback, dict) else None
    persisted_adjustments = (
        feedback_details.get("adjustments", [])
        if isinstance(feedback_details, dict) and "adjustments" in feedback_details
        else _adjustment_rows(before, persisted_weights, deltas)
    )
    state.post_trip = PostTripState(
        eligible=True,
        eligibleAt=(state.preferences.end_date + timedelta(days=1)).isoformat(),
        rating=feedback.get("rating", body.overall_rating),
        submittedAt=feedback.get("created_at"),
        adjustments=persisted_adjustments,
    )
    set_post_trip(trip_id, state.post_trip.model_dump(mode="json", by_alias=False))
    return {
        "postTrip": state.post_trip.model_dump(mode="json", by_alias=True),
        "profile": profiles.get_character_profile(user_id),
    }


@app.get("/api/trip/{trip_id}", response_model=TripState)
async def get_trip_state(trip_id: str, user_id: str = Depends(auth.get_current_user)) -> TripState:
    """Return the full current state of a trip.

    Includes preferences, research results, selections, and itinerary —
    whatever has been generated so far.
    """
    state = _get_owned_trip(trip_id, user_id)
    state.post_trip = _post_trip_state(state, user_id)
    return state


# serve the frontend, mounted last so /api routes win
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
