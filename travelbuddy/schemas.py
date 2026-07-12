"""Pydantic models for all TravelBuddy data structures."""

from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Input schemas ---

class BudgetTier(str, Enum):
    """Spending level for the trip."""
    BUDGET = "budget"
    MODERATE = "moderate"
    PREMIUM = "premium"
    LUXURY = "luxury"


class GroupType(str, Enum):
    """Type of travel group."""
    SOLO = "solo"
    COUPLE = "couple"
    FRIENDS = "friends"
    FAMILY = "family"


ALLOWED_VIBES = [
    "adventure", "culture", "food", "nightlife", "relaxation",
    "nature", "shopping", "history", "romance", "family-friendly",
]


class TripPreferences(BaseModel):
    """User-submitted trip preferences that drive all agent research."""
    destination: str = Field(..., description="City or region to visit")
    start_date: date
    end_date: date
    budget_tier: BudgetTier
    vibes: list[str] = Field(..., description="Interest tags from the allowed set")
    group_type: GroupType
    num_travelers: int = Field(..., ge=1)


# --- Agent output schemas ---

class Recommendation(BaseModel):
    """A single ranked recommendation returned by an agent."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: str = Field(..., description="hotel | activity | restaurant | transport")
    description: str = Field(..., description="2-3 compelling, specific sentences")
    reasoning: str = Field(..., description="Why this fits the user's preferences")
    estimated_cost: str = Field(..., description="Price range or estimate")
    rating: float = Field(..., ge=0, le=5, description="Rating out of 5 from web research")
    location: str = Field(..., description="Neighborhood or area within destination")
    image_search_query: str = Field(..., description="Query to find a representative photo")
    metadata: dict = Field(default_factory=dict, description="Agent-specific extra info")


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

class SelectionsRequest(BaseModel):
    """User's selected recommendations for itinerary generation."""
    preferences: TripPreferences
    selected_recommendation_ids: list[str] = Field(
        ..., description="UUIDs of chosen recommendations"
    )
    all_recommendations: list[Recommendation] = Field(
        ..., description="Full recommendation objects from the orchestrator"
    )


class SelectionsInput(BaseModel):
    """POST body for the /select endpoint."""
    selections: list[str] = Field(..., description="List of recommendation IDs the user picked")


class TripState(BaseModel):
    """Full state of a trip, persisted in the in-memory store."""
    trip_id: str
    preferences: TripPreferences
    context_brief: Optional[str] = None
    research_results: Optional[list[AgentResult]] = None
    research_errors: Optional[list[str]] = None
    selections: Optional[list[str]] = None
    itinerary: Optional[Itinerary] = None
    created_at: str
