"""Pydantic models for all TravelBuddy data structures."""

from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from personalization import (
    CANDIDATE_DEALBREAKER_TAGS,
    DIETARY_REQUIREMENTS,
    PROFILE_VIBES,
)


# --- Input schemas ---

class GroupType(str, Enum):
    """Type of travel group."""
    SOLO = "solo"
    COUPLE = "couple"
    FRIENDS = "friends"
    FAMILY = "family"


# The persistent profile uses the document's exact ten-vibe vocabulary.
# family-friendly remains a supported trip override for backwards compatibility;
# party suitability itself is a structured constraint, not a learned vibe.
ALLOWED_VIBES = [*PROFILE_VIBES, "family-friendly"]

# rough static rates to USD, good enough for budget scoring.
# also doubles as the list of currencies the app accepts
CURRENCY_TO_USD = {
    "USD": 1.0,
    "EUR": 1.10,
    "GBP": 1.30,
    "INR": 0.012,
    "JPY": 0.0067,
    "AUD": 0.66,
    "CAD": 0.73,
}


MAX_TRIP_DAYS = 30


class TripPreferences(BaseModel):
    """User-submitted trip preferences that drive all agent research."""
    destination: str = Field(..., min_length=1, description="City or region to visit")
    origin: str = Field(..., min_length=1, description="Where the traveler is starting from")
    start_date: date
    end_date: date
    budget_amount: float = Field(..., gt=0, description="Total budget for the whole trip, all travelers")
    currency: str = Field("USD", description="Currency code for budget_amount")
    vibes: list[str] = Field(..., description="Interest tags from the allowed set")
    group_type: GroupType
    num_travelers: int = Field(..., ge=1, le=50)
    cotravellers: list[str] = Field(
        default_factory=list,
        description="Saved co-traveller names to bring on this trip",
    )

    @model_validator(mode="after")
    def check_everything(self) -> "TripPreferences":
        if len(self.cotravellers) > 8:
            raise ValueError("Too many co-travellers (max 8)")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > MAX_TRIP_DAYS:
            raise ValueError(f"Trips longer than {MAX_TRIP_DAYS} days are not supported")
        if not self.vibes:
            raise ValueError("At least one vibe is required")
        for vibe in self.vibes:
            if vibe not in ALLOWED_VIBES:
                raise ValueError(f"Unknown vibe '{vibe}'. Allowed vibes: {ALLOWED_VIBES}")
        self.currency = self.currency.upper()
        if self.currency not in CURRENCY_TO_USD:
            raise ValueError(
                f"Unsupported currency '{self.currency}'. Supported: {list(CURRENCY_TO_USD)}"
            )
        return self


# --- Agent output schemas ---

class Recommendation(BaseModel):
    """A single ranked recommendation returned by an agent."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: str = Field(..., description="hotel | activity | restaurant | transport")
    description: str = Field(..., description="2-3 compelling, specific sentences")
    reasoning: str = Field(..., description="Why this fits the user's preferences")
    estimated_cost: str = Field(..., description="Human-readable price range, e.g. '$120-$180 per night'")
    cost_min: float = Field(0, ge=0, description="Low end of the cost estimate in USD")
    cost_max: float = Field(0, ge=0, description="High end of the cost estimate in USD")
    rating: float = Field(..., ge=0, le=5, description="Rating out of 5 from web research")
    review_count: int = Field(0, ge=0, description="Approximate number of reviews behind the rating")
    location: str = Field(..., description="Neighborhood or area within destination")
    image_search_query: str = Field(..., description="Query to find a representative photo")
    metadata: dict = Field(default_factory=dict, description="Agent-specific extra info")
    vibe_tags: list[str] = Field(
        default_factory=list,
        description="Controlled profile-vibe tags verified for this candidate",
    )
    constraint_tags: list[str] = Field(
        default_factory=list,
        description="Controlled hard-constraint tags carried by this candidate",
    )
    dietary_tags: list[str] = Field(
        default_factory=list,
        description="Verified dietary requirements this candidate can accommodate",
    )
    # Transitional aliases for older stored research payloads. New agents emit
    # constraint_tags and dietary_tags; the ranker reads both during migration.
    dealbreaker_tags: list[str] = Field(default_factory=list)
    dietary_accommodations: list[str] = Field(default_factory=list)
    dietary_conflicts: list[str] = Field(
        default_factory=list,
        description="Verified dietary requirements this candidate conflicts with",
    )

    # these get filled in by ranking.py, not the LLM
    rank: int = Field(0, description="1 = best. Assigned by our ranking algorithm, not the LLM")
    score: float = Field(0.0, description="Composite 0..1 score from the ranking algorithm")
    score_breakdown: dict = Field(default_factory=dict, description="Per-signal scores: rating/vibes/budget/total")

    @field_validator("vibe_tags")
    @classmethod
    def controlled_vibe_tags(cls, value: list[str]) -> list[str]:
        clean = list(dict.fromkeys(str(tag).strip().lower() for tag in value))
        invalid = [tag for tag in clean if tag not in PROFILE_VIBES]
        if invalid:
            raise ValueError(f"Unknown vibe tags: {invalid}")
        return clean

    @field_validator("constraint_tags", "dealbreaker_tags")
    @classmethod
    def controlled_dealbreaker_tags(cls, value: list[str]) -> list[str]:
        clean = list(dict.fromkeys(str(tag).strip().lower() for tag in value))
        invalid = [tag for tag in clean if tag not in CANDIDATE_DEALBREAKER_TAGS]
        if invalid:
            raise ValueError(f"Unknown dealbreaker tags: {invalid}")
        return clean

    @field_validator("dietary_tags", "dietary_accommodations", "dietary_conflicts")
    @classmethod
    def controlled_dietary_tags(cls, value: list[str]) -> list[str]:
        clean = list(dict.fromkeys(str(tag).strip().lower() for tag in value))
        invalid = [tag for tag in clean if tag not in DIETARY_REQUIREMENTS]
        if invalid:
            raise ValueError(f"Unknown dietary tags: {invalid}")
        return clean


class AgentResult(BaseModel):
    """Output from a single specialist agent."""
    agent_name: str
    recommendations: list[Recommendation] = Field(..., min_length=3, max_length=3)


class OrchestratorResult(BaseModel):
    """Combined output from all agents after parallel execution."""
    trip_context_brief: str
    agent_results: list[AgentResult]
    errors: list[str] = Field(default_factory=list, description="Agents that failed")


# --- Itinerary schemas ---

class ItineraryItem(BaseModel):
    """A single time-slotted entry in a day plan."""
    time_slot: str = Field(..., description='e.g. "9:00 AM - 11:30 AM"')
    title: str
    description: str
    category: str = Field(
        ..., description="accommodation | activity | restaurant | transport | free_time"
    )
    cost_estimate: str
    location: str
    tip: Optional[str] = Field(None, description="Optional local tip")


class DayPlan(BaseModel):
    """Plan for a single day of the trip."""
    day_number: int
    date: str
    theme: str = Field(..., description='e.g. "Temple District & Traditional Dining"')
    items: list[ItineraryItem]


class Itinerary(BaseModel):
    """Full day-by-day trip itinerary."""
    trip_title: str
    trip_summary: str = Field(..., description="2-3 sentence overview")
    days: list[DayPlan]


# --- API request/response wrappers ---

class SelectionsInput(BaseModel):
    """POST body for the /select endpoint."""
    selections: list[str] = Field(..., description="List of recommendation IDs the user picked")


# --- Auth / profile request bodies ---

class RegisterInput(BaseModel):
    """POST body for /auth/register."""
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)

    @model_validator(mode="after")
    def check_email(self) -> "RegisterInput":
        if "@" not in self.email or "." not in self.email:
            raise ValueError("That doesn't look like an email address")
        return self


class LoginInput(BaseModel):
    """POST body for /auth/login."""
    email: str
    password: str


class ChatInput(BaseModel):
    """POST body for one turn of the profile intake chat."""
    message: str = ""
    cotraveller_name: Optional[str] = Field(
        None, description="Set to build a co-traveller's sketch instead of the user's"
    )


class CharacterProfileUpdate(BaseModel):
    """Editable, user-facing fields from the persistent character profile."""
    model_config = ConfigDict(populate_by_name=True)

    summary: str = Field(..., min_length=20, max_length=2000)
    traits: Optional[dict[str, str | float]] = None
    character_md: Optional[str] = Field(None, alias="characterMd", max_length=5000)
    weights: Optional[dict[str, Any]] = None
    expected_version: Optional[int] = Field(None, alias="expectedVersion", ge=1)


class RecommendationFeedbackInput(BaseModel):
    """An owned recommendation-card signal resolved server-side by ID."""
    trip_id: str = Field(..., min_length=1, max_length=128)
    recommendation_id: str = Field(..., min_length=1, max_length=128)
    sentiment: str = Field(..., pattern="^(like|dislike)$")


class IntakeAnswerInput(BaseModel):
    """Value body for PUT /intake/answers/{question_id}."""
    value: Any


class PostTripFeedbackInput(BaseModel):
    """Post-trip rating; identity/idempotency are derived server-side."""
    model_config = ConfigDict(populate_by_name=True)

    overall_rating: Literal[1, 2, 3, 4, 5] = Field(..., alias="overallRating")


class PostTripState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eligible: bool = False
    eligible_at: Optional[str] = Field(None, alias="eligibleAt")
    rating: Optional[Literal[1, 2, 3, 4, 5]] = None
    submitted_at: Optional[str] = Field(None, alias="submittedAt")
    adjustments: list[dict[str, Any]] = Field(default_factory=list)


class TripState(BaseModel):
    """Full state of a trip, persisted in the in-memory store."""
    model_config = ConfigDict(populate_by_name=True)

    trip_id: str
    user_id: str = ""  # who owns this trip
    preferences: TripPreferences
    context_brief: Optional[str] = None
    research_results: Optional[list[AgentResult]] = None
    research_errors: Optional[list[str]] = None
    selections: Optional[list[str]] = None
    itinerary: Optional[Itinerary] = None
    post_trip: Optional[PostTripState] = Field(None, alias="postTrip")
    research_in_progress: bool = False
    created_at: str
