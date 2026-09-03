"""CheckIn — AI-powered travel planning API."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Header
from fastapi import Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import auth
import api_errors
import db
import profiles
from config import ALLOWED_ORIGINS, LOG_LEVEL, SSE_HEARTBEAT_SECONDS
from companions import (
    CompanionInviteInput,
    guest_companion_profiles,
    linked_companion_profiles,
    validate_trip_companions,
)
from inventory.models import AddCartItemInput, Cart, FlightInventory, HotelInventory
from inventory.providers import (
    InventoryProviderError,
    ProviderConfigurationError,
    ProviderItemUnavailableError,
)
from inventory.service import (
    InventoryDomainError,
    InventoryService,
    close_inventory_service,
    get_inventory_service,
)
from feasibility import check_feasibility
from inventory.service import CartVersionConflict, exact_cart_choices
from itinerary import generate_itinerary
from llm_client import get_llm_health, is_fatal_error
from orchestrator import agents_for_scope, generate_context_brief, run_agents_streaming
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
from schemas import IDEMPOTENCY_KEY_PATTERN, CreateTripInput, FeasibilityReport
from store import get_trip_by_idempotency_key
from store import ItineraryAlreadyRunning, finish_itinerary, selection_fingerprint, start_itinerary
from store import ItineraryInputsChanged
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

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close_inventory_service()


app = FastAPI(
    title="CheckIn",
    description="AI-powered travel planning with specialized research agents",
    version="0.3.0",
    lifespan=lifespan,
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


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _conflict_response(request: Request, *, code: str, message: str) -> JSONResponse:
    """A 409 with a specific public code (the generic handler only knows CONFLICT)."""
    return JSONResponse(
        status_code=409,
        content=api_errors.problem(request, code=code, message=message, retryable=True),
        headers={api_errors.REQUEST_ID_HEADER: api_errors.request_id_for(request)},
    )


def _stored_itinerary(raw: dict, fingerprint: str) -> Itinerary | None:
    """Return the saved itinerary only when it was built from the current inputs."""
    if not raw.get("itinerary") or raw.get("itinerary_fingerprint") != fingerprint:
        return None
    try:
        return Itinerary.model_validate(raw["itinerary"])
    except ValueError:
        return None


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


def _raise_inventory_failure(exc: Exception) -> None:
    """Translate supplier/domain failures without exposing provider payloads."""
    if isinstance(exc, InventoryDomainError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    if isinstance(exc, ProviderConfigurationError):
        raise HTTPException(
            status_code=503,
            detail="Live booking inventory is not connected on this deployment.",
        ) from exc
    if isinstance(exc, ProviderItemUnavailableError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, InventoryProviderError):
        raise HTTPException(
            status_code=503,
            detail="Live inventory could not be checked right now. Try again shortly.",
        ) from exc
    raise exc


# --- Auth endpoints ---

@app.get("/api/health")
async def health() -> dict:
    """Basic health check."""
    return {"status": "ok"}


@app.get("/api/health/agents")
async def agent_health(_user_id: str = Depends(auth.get_current_user)) -> dict:
    """Prompt-free circuit and latency state for operational monitoring."""
    return get_llm_health()


@app.post("/api/auth/register")
async def register(body: RegisterInput) -> dict:
    """Create an account and log straight in."""
    token = auth.register(body.email, body.password, body.username, body.name, body.phone)
    return {"token": token}


@app.post("/api/auth/login")
async def login(body: LoginInput) -> dict:
    """Log in with an email or username, get a session token."""
    token = auth.login(body.login_identifier, body.password)
    return {"token": token}


@app.post("/api/auth/logout")
async def logout(token: str = Depends(auth.get_token)) -> dict:
    auth.logout(token)
    return {"status": "logged_out"}


@app.get("/api/auth/me")
async def me(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Who am I, have I done the intake chat, who do I travel with."""
    account = db.get_user_by_id(user_id) or {}
    return {
        "email": account.get("email", ""),
        "username": account.get("username"),
        "name": account.get("name"),
        "phone": account.get("phone"),
        "intake_complete": profiles.load_sketch(user_id) is not None,
        "cotravellers": profiles.list_cotravellers(user_id),
    }


@app.get("/api/users/lookup")
async def lookup_user(username: str, user_id: str = Depends(auth.get_current_user)) -> dict:
    """Resolve a username to the minimum needed to invite them as a co-traveller."""
    record = db.get_user_by_username(username)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No CheckIn user named '@{username.strip().lstrip('@')}'")
    return {
        "username": record["username"],
        "name": record["name"],
        "intake_complete": profiles.load_sketch(record["user_id"]) is not None,
        "link_status": db.companion_link_status(user_id, record["user_id"]),
    }


# --- Companion invitations ---

def _companion_link_view(link: dict, viewer_id: str) -> dict:
    """Public row for the viewer: names the other traveler, never their profile."""
    counterpart_id = (
        link["invitee_user_id"] if link["inviter_user_id"] == viewer_id else link["inviter_user_id"]
    )
    counterpart = db.get_user_by_id(counterpart_id) or {}
    return {
        "link_id": link["link_id"],
        "username": counterpart.get("username"),
        "name": counterpart.get("name"),
        "status": link["status"],
        "created_at": link["created_at"],
        "responded_at": link["responded_at"],
    }


def _respond_to_companion_link(link_id: str, user_id: str, status: str) -> dict:
    link = db.get_companion_link_by_id(link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if link["invitee_user_id"] != user_id:
        raise HTTPException(
            status_code=403, detail="Only the invited traveler can respond to this invitation"
        )
    try:
        updated = db.respond_companion_link(link_id, user_id, status)
    except db.CompanionLinkNotPending as exc:
        raise HTTPException(
            status_code=409,
            detail="This invitation is no longer open. Ask the organizer to invite you again.",
        ) from exc
    return _companion_link_view(updated or link, user_id)


@app.get("/api/companions/links")
async def companion_links(user_id: str = Depends(auth.get_current_user)) -> dict:
    """Incoming and outgoing invitations from the caller's perspective."""
    return db.list_companion_links(user_id)


@app.post("/api/companions/links")
async def invite_companion(
    body: CompanionInviteInput, user_id: str = Depends(auth.get_current_user)
) -> dict:
    """Invite another account; a declined or revoked invitation becomes pending again."""
    invitee = db.get_user_by_username(body.username)
    if invitee is None:
        raise HTTPException(
            status_code=404,
            detail=f"No CheckIn user named '@{body.username.strip().lstrip('@')}'",
        )
    if invitee["user_id"] == user_id:
        raise HTTPException(
            status_code=400, detail="You can't invite yourself — add companions other than yourself"
        )
    link = db.create_or_reset_companion_link(user_id, invitee["user_id"])
    return _companion_link_view(link, user_id)


@app.post("/api/companions/links/{link_id}/accept")
async def accept_companion_link(link_id: str, user_id: str = Depends(auth.get_current_user)) -> dict:
    return _respond_to_companion_link(link_id, user_id, "accepted")


@app.post("/api/companions/links/{link_id}/decline")
async def decline_companion_link(link_id: str, user_id: str = Depends(auth.get_current_user)) -> dict:
    return _respond_to_companion_link(link_id, user_id, "declined")


@app.delete("/api/companions/links/{link_id}")
async def remove_companion_link(link_id: str, user_id: str = Depends(auth.get_current_user)) -> dict:
    """The inviter revokes, the invitee declines; either way the profile stops flowing."""
    link = db.get_companion_link_by_id(link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    if user_id not in (link["inviter_user_id"], link["invitee_user_id"]):
        raise HTTPException(status_code=403, detail="This invitation belongs to other travelers")
    updated = db.delete_companion_link(link_id, user_id)
    return _companion_link_view(updated or link, user_id)


# --- Profile endpoints ---

@app.post("/api/profile/chat")
async def profile_chat(body: ChatInput, user_id: str = Depends(auth.get_current_user)) -> dict:
    """One turn of the intake conversation with Buddy.

    Send an empty message to start. When done=true, the sketch is saved.
    Set cotraveller_name to build a co-traveller's sketch instead. Resending
    the same turn_key replays the stored reply instead of appending twice.
    """
    reply, done = await profiles.chat_turn(
        user_id, body.message, body.cotraveller_name, turn_key=body.turn_key
    )
    return {"reply": reply, "done": done}


@app.get("/api/profile/chat")
async def profile_chat_transcript(
    cotraveller_name: str = Query(..., min_length=1, max_length=120),
    user_id: str = Depends(auth.get_current_user),
) -> dict:
    """The saved guest intake thread, so a reload or server restart resumes it."""
    return profiles.guest_chat_transcript(user_id, cotraveller_name)


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
    body: CreateTripInput,
    user_id: str = Depends(auth.get_current_user),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict:
    """Create exactly one trip after an advisory feasibility check.

    A repeated ``Idempotency-Key`` replays the stored trip without another
    feasibility call. An ``unrealistic`` verdict holds the request (no trip
    row) until the client resubmits with ``feasibility_acknowledged``.
    """
    if idempotency_key is not None and not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must be 8-128 characters of letters, digits, '.', '_', ':' or '-'",
        )
    # intake chat is required once before planning
    if profiles.load_sketch(user_id) is None:
        raise HTTPException(status_code=403, detail="Finish the intake chat first")

    prefs = body.to_preferences()
    validate_trip_companions(user_id, prefs)

    if idempotency_key is not None:
        existing = get_trip_by_idempotency_key(user_id, idempotency_key)
        if existing is not None:
            replayed_feasibility = existing.feasibility or FeasibilityReport(verdict="unchecked")
            return {
                "trip_id": existing.trip_id,
                "status": "received",
                "replayed": True,
                "preferences": existing.preferences.model_dump(mode="json"),
                "feasibility": replayed_feasibility.model_dump(mode="json"),
            }

    if body.feasibility_acknowledged:
        feasibility = FeasibilityReport(verdict="unchecked")
    else:
        feasibility = await check_feasibility(prefs)
        if feasibility.verdict == "unrealistic":
            logger.info("Trip held for %s: feasibility=unrealistic", prefs.destination)
            return {
                "trip_id": None,
                "status": "held",
                "replayed": False,
                "preferences": prefs.model_dump(mode="json"),
                "feasibility": feasibility.model_dump(mode="json"),
            }

    state = create_trip(prefs, user_id, idempotency_key=idempotency_key, feasibility=feasibility)
    logger.info(
        "Trip created: %s → %s feasibility=%s",
        state.trip_id,
        prefs.destination,
        feasibility.verdict,
    )
    return {
        "trip_id": state.trip_id,
        "status": "received",
        "replayed": False,
        "preferences": prefs.model_dump(mode="json"),
        "feasibility": feasibility.model_dump(mode="json"),
    }


# --- Supplier inventory and saved cart ---

@app.get(
    "/api/trip/{trip_id}/hotels/{recommendation_id}/rates",
    response_model=HotelInventory,
)
async def get_hotel_rates(
    trip_id: str,
    recommendation_id: str,
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> HotelInventory:
    """Check dated room types and exact supplier prices for an owned recommendation."""
    state = _get_owned_trip(trip_id, user_id)
    try:
        return await service.hotel_rates(state, recommendation_id)
    except (InventoryDomainError, InventoryProviderError) as exc:
        _raise_inventory_failure(exc)


@app.get(
    "/api/trip/{trip_id}/flights/{recommendation_id}/offers",
    response_model=FlightInventory,
)
async def get_flight_offers(
    trip_id: str,
    recommendation_id: str,
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> FlightInventory:
    """Check dated supplier flight offers for an owned transport recommendation."""
    state = _get_owned_trip(trip_id, user_id)
    try:
        return await service.flight_offers(state, recommendation_id)
    except (InventoryDomainError, InventoryProviderError) as exc:
        _raise_inventory_failure(exc)


@app.get("/api/trip/{trip_id}/cart", response_model=Cart)
async def get_trip_cart(
    trip_id: str,
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> Cart:
    """Read the owned trip's saved shortlist and independent expiry clocks."""
    return service.cart(_get_owned_trip(trip_id, user_id))


@app.post("/api/trip/{trip_id}/cart/items", response_model=Cart)
async def add_trip_cart_item(
    trip_id: str,
    body: AddCartItemInput,
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> Cart:
    """Save an exact server-verified rate/offer or a non-bookable recommendation."""
    state = _get_owned_trip(trip_id, user_id)
    try:
        return await service.add_item(state, body)
    except (InventoryDomainError, InventoryProviderError) as exc:
        _raise_inventory_failure(exc)


@app.delete("/api/trip/{trip_id}/cart/items/{item_id}", response_model=Cart)
async def remove_trip_cart_item(
    trip_id: str,
    item_id: str,
    request: Request,
    expected_version: int | None = Query(None, alias="expectedVersion", ge=1),
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> Cart:
    """Remove one item from an owned trip's saved shortlist.

    ``expectedVersion`` lets a client refuse to act on a cart it has not seen.
    """
    state = _get_owned_trip(trip_id, user_id)
    try:
        return service.remove_item(state, item_id, expected_version=expected_version)
    except CartVersionConflict as exc:
        return _conflict_response(request, code=exc.code or "CONFLICT", message=str(exc))
    except InventoryDomainError as exc:
        _raise_inventory_failure(exc)


@app.post("/api/trip/{trip_id}/cart/revalidate", response_model=Cart)
async def revalidate_trip_cart(
    trip_id: str,
    user_id: str = Depends(auth.get_current_user),
    service: InventoryService = Depends(get_inventory_service),
) -> Cart:
    """Recheck every supplier item and surface price/availability changes."""
    return await service.revalidate(_get_owned_trip(trip_id, user_id))


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
    scoped_agents = agents_for_scope(state.preferences.scope)
    failed_agents = {
        name
        for name in scoped_agents
        if any(
            error.startswith(name)
            for error in (state.research_errors or [])
        )
    }
    completed_agents = {
        result.agent_name for result in (state.research_results or [])
        if result.agent_name in scoped_agents
    }.difference(failed_agents)
    resume_partial = 0 < len(completed_agents) < len(scoped_agents)
    # A deliberate full refresh replaces every category: bypass the research
    # cache lookup but still store the fresh results.
    full_refresh = len(completed_agents) == len(scoped_agents)
    target_agents = (
        [name for name in scoped_agents if name not in completed_agents]
        if resume_partial
        else list(scoped_agents)
    )
    # taste profile: prose goes into the brief (light aim for the web
    # search), taste vectors go into the ranker (the heavy lifting)
    raw_sketch = profiles.load_sketch(user_id)
    brief_sketch = None
    user_taste = None
    if raw_sketch:
        brief_sketch, _ = profiles.parse_taste(raw_sketch)  # prose only, no json block
        user_taste = profiles.get_taste(user_id)

    guest_sketches, guest_tastes = guest_companion_profiles(
        user_id, state.preferences.cotravellers
    )
    _linked_sketches, linked_tastes = linked_companion_profiles(
        user_id, getattr(state.preferences, "cotraveller_usernames", []) or []
    )
    # The brief is streamed and stored for the organizer, so a linked member's
    # prose never enters it; their compiled taste still shapes the ranking.
    cotraveller_sketches = [*guest_sketches]
    cotraveller_tastes = [*guest_tastes, *linked_tastes]

    try:
        # Acquire only after profile preparation, so a profile/database error
        # cannot leave a lease stranded before the stream cleanup exists.
        research_lease_id = start_research(
            trip_id,
            preserve_results=True,
            preserve_downstream=resume_partial,
        )
    except ResearchAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def event_stream():
        try:
            yield _sse({
                "event": "research_started",
                "resumed": resume_partial,
                "agents": target_agents,
            })

            # Reusing the brief makes a missing-category retry both faster and
            # less expensive. New/full runs have a deterministic AI fallback.
            if resume_partial and state.context_brief:
                context_brief = state.context_brief
            else:
                context_brief = await generate_context_brief(
                    state.preferences, brief_sketch, cotraveller_sketches
                )
                set_context_brief(trip_id, context_brief, research_lease_id)

            yield _sse({"event": "context_brief_generated", "brief": context_brief})

            # Stream agent results as each completes
            async for event in run_agents_streaming(
                state.preferences,
                context_brief,
                user_taste,
                cotraveller_tastes,
                agent_names=target_agents,
                force_refresh=full_refresh,
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
                    event["trip_id"] = trip_id
                    latest = get_trip(trip_id)
                    event["available_categories"] = sum(
                        1 for result in (latest.research_results or [])
                        if result.agent_name in scoped_agents
                    ) if latest else 0

                yield _sse(event)
        except Exception as exc:
            # response already started, so send an error event instead of just dying
            logger.error(
                "[%s] Research stream failed: %s",
                request.state.request_id,
                type(exc).__name__,
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
                retryable=not is_fatal_error(exc),
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
    selections = list(dict.fromkeys(body.selections))
    invalid = [sid for sid in selections if sid not in valid_ids]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown recommendation IDs: {invalid}",
        )

    set_selections(trip_id, selections)
    logger.info("Trip %s: %d selections saved", trip_id, len(selections))
    return {"status": "selections_saved", "count": len(selections)}


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

    selected = _selected_recommendations(state)
    if not selected:
        raise HTTPException(status_code=400, detail="Selected IDs did not match any recommendations")

    raw = db.load_trip_state(trip_id) or {}
    fingerprint = selection_fingerprint(raw)
    stored = _stored_itinerary(raw, fingerprint)
    if stored is not None:
        logger.info("Trip %s: replaying the stored itinerary for unchanged selections", trip_id)

        async def replay_stream():
            yield _sse({
                "event": "itinerary_started",
                "trip_id": trip_id,
                "selection_count": len(selected),
                "replayed": True,
            })
            yield _sse({
                "event": "itinerary_complete",
                "itinerary": stored.model_dump(mode="json"),
                "replayed": True,
            })

        return StreamingResponse(replay_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)

    try:
        lease_id = start_itinerary(trip_id)
    except ItineraryAlreadyRunning:
        return _conflict_response(
            request,
            code="ITINERARY_IN_PROGRESS",
            message="An itinerary is already being generated for this trip. Wait for it to finish before building again.",
        )

    exact_choices = exact_cart_choices(raw)
    context_brief = state.context_brief or ""

    async def event_stream():
        yield _sse({"event": "itinerary_started", "trip_id": trip_id, "selection_count": len(selected)})

        task = asyncio.create_task(
            generate_itinerary(state.preferences, context_brief, selected, exact_choices=exact_choices)
        )
        try:
            while not task.done():
                done, _ = await asyncio.wait(
                    {task},
                    timeout=SSE_HEARTBEAT_SECONDS,
                )
                if not done:
                    yield _sse({"event": "heartbeat"})
            itinerary = await task
            set_itinerary(trip_id, itinerary, lease_id, fingerprint)
            yield _sse({
                "event": "itinerary_complete",
                "itinerary": itinerary.model_dump(mode="json"),
            })
        except ItineraryInputsChanged:
            logger.info("Trip %s: selections changed during the build; itinerary discarded", trip_id)
            yield _sse(api_errors.stream_problem(
                request,
                event="itinerary_failed",
                code="ITINERARY_INPUTS_CHANGED",
                message=(
                    "Your selections changed while the itinerary was being built, so it was "
                    "discarded. Build again to include the latest choices."
                ),
                retryable=True,
            ))
        except Exception as exc:
            logger.error(
                "[%s] Itinerary generation failed: %s",
                request.state.request_id,
                type(exc).__name__,
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
                retryable=not is_fatal_error(exc),
            ))
        finally:
            # Release before any await: a client disconnect cancels this
            # generator on every tick, so nothing after an await is reliable.
            finish_itinerary(trip_id, lease_id)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

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
