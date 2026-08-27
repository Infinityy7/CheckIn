"""Deterministic questionnaire compilation and conservative profile learning.

The character sketch is retrieval context.  The structured profile is the only
input to ranking and learning; Q9 free text is deliberately absent from scores.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, Field, field_validator, model_validator


PROFILE_SCHEMA_VERSION = 1

PROFILE_VIBES = (
    "adventure", "culture", "food", "nightlife", "relaxation",
    "nature", "shopping", "history", "romance", "wellness",
)
SPEND_CATEGORIES = ("stay", "experiences", "food", "shopping", "transport")
DEALBREAKER_TAGS = (
    "early_flights", "theme_parks", "long_bus_rides",
    "crowded_spots", "heights", "boats",
)
CANDIDATE_DEALBREAKER_TAGS = DEALBREAKER_TAGS + (
    "kid_unfriendly", "group_unfriendly",
)
DIETARY_REQUIREMENTS = (
    "vegetarian", "vegan", "halal", "kosher", "gluten_free",
    "dairy_free", "nut_allergy", "shellfish_allergy",
)


class Chronotype(str, Enum):
    EARLY = "early"
    MID = "mid"
    LATE = "late"


class Archetype(str, Enum):
    FOODIE_EXPLORER = "foodie_explorer"
    CULTURE_SEEKER = "culture_seeker"
    ADRENALINE_CHASER = "adrenaline_chaser"
    SLOW_TRAVELER = "slow_traveler"
    LUXURY_UNWINDER = "luxury_unwinder"
    SOCIAL_BUTTERFLY = "social_butterfly"


class PartyShape(str, Enum):
    SOLO = "solo"
    PARTNER = "partner"
    FRIENDS = "friends"
    FAMILY_YOUNG_KIDS = "family_young_kids"
    MULTI_GENERATION = "multi_generation"


ARCHETYPE_PRIORS: dict[str, dict[str, float]] = {
    Archetype.FOODIE_EXPLORER.value: {
        "food": 0.55, "culture": 0.15, "adventure": 0.10,
        "nightlife": 0.10, "history": 0.05, "shopping": 0.05,
    },
    Archetype.CULTURE_SEEKER.value: {
        "culture": 0.45, "history": 0.30, "food": 0.10,
        "romance": 0.05, "shopping": 0.05, "wellness": 0.05,
    },
    Archetype.ADRENALINE_CHASER.value: {
        "adventure": 0.60, "nature": 0.20, "nightlife": 0.10,
        "culture": 0.05, "food": 0.05,
    },
    Archetype.SLOW_TRAVELER.value: {
        "relaxation": 0.45, "nature": 0.25, "wellness": 0.20,
        "romance": 0.10,
    },
    Archetype.LUXURY_UNWINDER.value: {
        "relaxation": 0.35, "wellness": 0.25, "romance": 0.20,
        "food": 0.10, "shopping": 0.10,
    },
    Archetype.SOCIAL_BUTTERFLY.value: {
        "nightlife": 0.45, "food": 0.20, "culture": 0.15,
        "shopping": 0.10, "adventure": 0.10,
    },
}


class QuestionnaireAnswers(BaseModel):
    """The exact nine-question v1 onboarding payload.

    Q1 follows the displayed slider direction: 0 is planned, 1 is
    spontaneous.  Despite its legacy public ID (pace_score), it is stored as
    spontaneity and never relabeled as itinerary density.
    """

    q1_planned_to_spontaneous: float = Field(ge=0, le=1)
    q2_top_vibes: list[str] = Field(min_length=3, max_length=3)
    q3_splurge_category: str
    q3_save_category: str
    q4_chronotype: Chronotype
    q5_archetype: Archetype
    q6_default_party: PartyShape
    q7_food_adventurousness: float = Field(ge=0, le=1)
    q8_dealbreakers: list[str] = Field(default_factory=list)
    q8_dietary_requirements: list[str] = Field(default_factory=list)
    q9_perfect_moment: str | None = Field(default=None, max_length=280)

    @field_validator("q2_top_vibes")
    @classmethod
    def validate_top_vibes(cls, value: list[str]) -> list[str]:
        values = [str(item).strip().lower() for item in value]
        if len(set(values)) != 3:
            raise ValueError("Q2 must contain exactly three distinct vibes")
        invalid = [item for item in values if item not in PROFILE_VIBES]
        if invalid:
            raise ValueError(f"Unknown Q2 vibes: {invalid}")
        return values

    @field_validator("q3_splurge_category", "q3_save_category")
    @classmethod
    def validate_spend_category(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SPEND_CATEGORIES:
            raise ValueError(f"Unknown spend category: {value}")
        return value

    @field_validator("q8_dealbreakers")
    @classmethod
    def validate_dealbreakers(cls, value: list[str]) -> list[str]:
        values = list(dict.fromkeys(str(item).strip().lower() for item in value))
        invalid = [item for item in values if item not in DEALBREAKER_TAGS]
        if invalid:
            raise ValueError(f"Unknown dealbreakers: {invalid}")
        return values

    @field_validator("q8_dietary_requirements")
    @classmethod
    def validate_dietary_requirements(cls, value: list[str]) -> list[str]:
        values = list(dict.fromkeys(str(item).strip().lower() for item in value))
        invalid = [item for item in values if item not in DIETARY_REQUIREMENTS]
        if invalid:
            raise ValueError(f"Unknown dietary requirements: {invalid}")
        return values

    @field_validator("q9_perfect_moment")
    @classmethod
    def clean_perfect_moment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = re.sub(r"\s+", " ", value).strip()
        return value or None

    @model_validator(mode="after")
    def distinct_spend_categories(self) -> "QuestionnaireAnswers":
        if self.q3_splurge_category == self.q3_save_category:
            raise ValueError("Splurge and save categories must differ")
        return self


class ProfileWeights(BaseModel):
    schema_version: int = PROFILE_SCHEMA_VERSION
    vibe_weights: dict[str, float]
    spontaneity: float = Field(ge=0, le=1)
    pace_score: float = Field(ge=0, le=1)
    chronotype: Chronotype
    splurge_category: str
    save_category: str
    archetype: Archetype
    default_party: PartyShape
    food_adventurousness: float = Field(ge=0, le=1)
    dealbreakers: list[str] = Field(default_factory=list)
    dietary_requirements: list[str] = Field(default_factory=list)
    raw_answers: dict[str, Any] = Field(default_factory=dict)

    @field_validator("vibe_weights")
    @classmethod
    def validate_vibe_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if set(value) != set(PROFILE_VIBES):
            raise ValueError("vibe_weights must contain every controlled profile vibe")
        clean = {key: max(0.0, float(value[key])) for key in PROFILE_VIBES}
        total = sum(clean.values())
        if total <= 0:
            raise ValueError("vibe_weights must contain positive weight")
        return {key: clean[key] / total for key in PROFILE_VIBES}


class PersonalizationArtifacts(BaseModel):
    character_md: str
    weights: ProfileWeights


QUESTION_IDS = (
    "spontaneity", "top_vibes", "spend_preferences", "chronotype",
    "archetype", "default_party", "food_adventurousness", "constraints",
    "perfect_moment",
)
REQUIRED_QUESTION_IDS = QUESTION_IDS

QUESTIONNAIRE = (
    {
        "id": "spontaneity",
        "prompt": "Your ideal trip day: every hour planned, or see where the day takes you?",
        "type": "slider", "lowLabel": "Planned", "highLabel": "Spontaneous",
    },
    {
        "id": "top_vibes", "prompt": "Pick your top 3 — what makes a trip unforgettable?",
        "type": "multi_choice", "minSelections": 3, "maxSelections": 3,
        "options": [{"value": value, "label": value.replace("_", " ").title()} for value in PROFILE_VIBES],
    },
    {
        "id": "spend_preferences", "prompt": "You’d happily splurge on ___ but save on ___",
        "type": "paired_choice",
        "options": [{"value": value, "label": value.title()} for value in SPEND_CATEGORIES],
    },
    {
        "id": "chronotype", "prompt": "On holiday you’re up and out by…",
        "type": "single_choice", "options": [
            {"value": "early", "label": "8 AM"},
            {"value": "mid", "label": "9:30ish"},
            {"value": "late", "label": "Whenever we wake up"},
        ],
    },
    {
        "id": "archetype", "prompt": "Which traveler is most you?",
        "type": "single_choice",
        "options": [{"value": value.value, "label": value.value.replace("_", " ").title()} for value in Archetype],
    },
    {
        "id": "default_party", "prompt": "Who do you usually travel with?",
        "type": "single_choice", "options": [
            {"value": "solo", "label": "Solo"}, {"value": "partner", "label": "Partner"},
            {"value": "friends", "label": "Friends"},
            {"value": "family_young_kids", "label": "Family with young kids"},
            {"value": "multi_generation", "label": "Multi-generation"},
        ],
    },
    {
        "id": "food_adventurousness",
        "prompt": "Food on trips: stick to what you know, or eat like a local dares you to?",
        "type": "slider", "lowLabel": "Familiar", "highLabel": "Anything",
    },
    {
        "id": "constraints", "prompt": "Any absolute no-gos?",
        "type": "multi_choice", "minSelections": 0,
        "options": [
            {"value": value, "label": value.replace("_", " ").title()}
            for value in (*DEALBREAKER_TAGS, *DIETARY_REQUIREMENTS)
        ],
    },
    {
        "id": "perfect_moment", "prompt": "In one line — describe your perfect travel moment.",
        "type": "free_text", "optional": True,
    },
)

ARCHETYPE_PACE = {
    Archetype.FOODIE_EXPLORER.value: 0.55,
    Archetype.CULTURE_SEEKER.value: 0.50,
    Archetype.ADRENALINE_CHASER.value: 0.85,
    Archetype.SLOW_TRAVELER.value: 0.25,
    Archetype.LUXURY_UNWINDER.value: 0.40,
    Archetype.SOCIAL_BUTTERFLY.value: 0.72,
}
PARTY_PACE = {
    PartyShape.SOLO.value: 0.58,
    PartyShape.PARTNER.value: 0.50,
    PartyShape.FRIENDS.value: 0.68,
    PartyShape.FAMILY_YOUNG_KIDS.value: 0.30,
    PartyShape.MULTI_GENERATION.value: 0.35,
}


def _normalized_vibe_prior(top_vibes: Iterable[str], archetype: str) -> dict[str, float]:
    # Explicit picks carry 70% of the prior; the archetype fills the remaining
    # 30%.  This keeps the stereotype subordinate to answers the user chose.
    weights = {vibe: 0.0 for vibe in PROFILE_VIBES}
    for vibe in top_vibes:
        weights[vibe] += 0.70 / 3
    for vibe, prior in ARCHETYPE_PRIORS[archetype].items():
        weights[vibe] += 0.30 * prior
    total = sum(weights.values())
    return {vibe: weights[vibe] / total for vibe in PROFILE_VIBES}


def _pace_label(score: float) -> str:
    if score < 0.34:
        return "unhurried"
    if score > 0.66:
        return "full-paced"
    return "balanced"


def render_character_markdown(answers: QuestionnaireAnswers, weights: ProfileWeights) -> str:
    """Create retrieval prose without embedding the machine scoring payload."""
    vibe_text = ", ".join(answers.q2_top_vibes[:-1]) + f", and {answers.q2_top_vibes[-1]}"
    start = {
        Chronotype.EARLY: "early starts",
        Chronotype.MID: "mid-morning starts",
        Chronotype.LATE: "late, unhurried starts",
    }[answers.q4_chronotype]
    food = (
        "adventurous local food" if answers.q7_food_adventurousness >= 0.67
        else "a mix of local discoveries and familiar food" if answers.q7_food_adventurousness >= 0.34
        else "familiar, low-risk food choices"
    )
    party = answers.q6_default_party.value.replace("_", " ")
    lines = [
        "# Character Sketch",
        "",
        f"A {_pace_label(weights.pace_score)} traveler drawn most strongly to {vibe_text}.",
        f"They prefer {start}, usually travel with {party}, and look for {food}.",
        f"They are happy to splurge on {answers.q3_splurge_category} while saving on {answers.q3_save_category}.",
    ]
    if answers.q8_dealbreakers:
        lines.append("Absolute no-gos: " + ", ".join(tag.replace("_", " ") for tag in answers.q8_dealbreakers) + ".")
    if answers.q8_dietary_requirements:
        lines.append("Dietary requirements: " + ", ".join(tag.replace("_", " ") for tag in answers.q8_dietary_requirements) + ".")
    if answers.q9_perfect_moment:
        lines.extend(["", "Perfect travel moment (retrieval flavour only): " + answers.q9_perfect_moment])
    return "\n".join(lines).strip() + "\n"


def compile_questionnaire(answers: QuestionnaireAnswers | dict[str, Any]) -> PersonalizationArtifacts:
    parsed = answers if isinstance(answers, QuestionnaireAnswers) else QuestionnaireAnswers.model_validate(answers)
    # Q1 is spontaneity: 0 planned -> 1 spontaneous. Daily itinerary density is
    # separately derived from archetype + party shape.
    pace_score = (
        0.75 * ARCHETYPE_PACE[parsed.q5_archetype.value]
        + 0.25 * PARTY_PACE[parsed.q6_default_party.value]
    )
    raw_answers = {
        "spontaneity": parsed.q1_planned_to_spontaneous,
        "top_vibes": parsed.q2_top_vibes,
        "spend_preferences": {
            "splurge": parsed.q3_splurge_category,
            "save": parsed.q3_save_category,
        },
        "chronotype": parsed.q4_chronotype.value,
        "archetype": parsed.q5_archetype.value,
        "default_party": parsed.q6_default_party.value,
        "food_adventurousness": parsed.q7_food_adventurousness,
        "constraints": [*parsed.q8_dealbreakers, *parsed.q8_dietary_requirements],
        "perfect_moment": parsed.q9_perfect_moment,
    }
    weights = ProfileWeights(
        vibe_weights=_normalized_vibe_prior(parsed.q2_top_vibes, parsed.q5_archetype.value),
        spontaneity=parsed.q1_planned_to_spontaneous,
        pace_score=pace_score,
        chronotype=parsed.q4_chronotype,
        splurge_category=parsed.q3_splurge_category,
        save_category=parsed.q3_save_category,
        archetype=parsed.q5_archetype,
        default_party=parsed.q6_default_party,
        food_adventurousness=parsed.q7_food_adventurousness,
        dealbreakers=parsed.q8_dealbreakers,
        dietary_requirements=parsed.q8_dietary_requirements,
        raw_answers=raw_answers,
    )
    return PersonalizationArtifacts(
        character_md=render_character_markdown(parsed, weights),
        weights=weights,
    )


def questionnaire_from_saved_answers(answers: dict[str, Any]) -> QuestionnaireAnswers:
    missing = [question_id for question_id in REQUIRED_QUESTION_IDS if question_id not in answers]
    # Q9 is optional but the question still needs a recorded null/empty response.
    if missing:
        raise ValueError(f"Missing questionnaire answers: {missing}")
    spend = answers["spend_preferences"]
    if not isinstance(spend, dict):
        raise ValueError("spend_preferences must be an object with splurge and save")
    constraints = answers["constraints"]
    if not isinstance(constraints, list):
        raise ValueError("constraints must be a list")
    payload = {
        "q1_planned_to_spontaneous": answers["spontaneity"],
        "q2_top_vibes": answers["top_vibes"],
        "q3_splurge_category": spend.get("splurge"),
        "q3_save_category": spend.get("save"),
        "q4_chronotype": answers["chronotype"],
        "q5_archetype": answers["archetype"],
        "q6_default_party": answers["default_party"],
        "q7_food_adventurousness": answers["food_adventurousness"],
        "q8_dealbreakers": [item for item in constraints if item in DEALBREAKER_TAGS],
        "q8_dietary_requirements": [item for item in constraints if item in DIETARY_REQUIREMENTS],
        "q9_perfect_moment": answers["perfect_moment"],
    }
    unknown_constraints = [
        item for item in constraints
        if item not in DEALBREAKER_TAGS and item not in DIETARY_REQUIREMENTS
    ]
    if unknown_constraints:
        raise ValueError(f"Unknown constraints: {unknown_constraints}")
    return QuestionnaireAnswers.model_validate(payload)


def validate_saved_answer(question_id: str, value: Any) -> Any:
    """Validate one answer without requiring the other eight answers yet."""
    if question_id not in QUESTION_IDS:
        raise ValueError(f"Unknown question_id: {question_id}")
    sample = {
        "spontaneity": 0.5,
        "top_vibes": ["culture", "food", "nature"],
        "spend_preferences": {"splurge": "experiences", "save": "transport"},
        "chronotype": "mid",
        "archetype": "culture_seeker",
        "default_party": "solo",
        "food_adventurousness": 0.5,
        "constraints": [],
        "perfect_moment": None,
    }
    sample[question_id] = value
    parsed = questionnaire_from_saved_answers(sample)
    canonical = {
        "spontaneity": parsed.q1_planned_to_spontaneous,
        "top_vibes": parsed.q2_top_vibes,
        "spend_preferences": {
            "splurge": parsed.q3_splurge_category,
            "save": parsed.q3_save_category,
        },
        "chronotype": parsed.q4_chronotype.value,
        "archetype": parsed.q5_archetype.value,
        "default_party": parsed.q6_default_party.value,
        "food_adventurousness": parsed.q7_food_adventurousness,
        "constraints": [*parsed.q8_dealbreakers, *parsed.q8_dietary_requirements],
        "perfect_moment": parsed.q9_perfect_moment,
    }
    return canonical[question_id]


SELECTION_DELTA = 0.012
RATING_STEP = 0.012
MAX_TAG_BATCH_DELTA = 0.05
WEIGHT_FLOOR = 0.0001


def _recommendation_tags(recommendation: Any) -> list[str]:
    if isinstance(recommendation, dict):
        raw = recommendation.get("vibe_tags", [])
    else:
        raw = getattr(recommendation, "vibe_tags", [])
    return list(dict.fromkeys(str(tag) for tag in raw if str(tag) in PROFILE_VIBES))


def _recommendation_key(recommendation: Any) -> str:
    if isinstance(recommendation, dict):
        return str(recommendation.get("id") or recommendation.get("name") or id(recommendation))
    return str(getattr(recommendation, "id", None) or getattr(recommendation, "name", None) or id(recommendation))


def learn_from_selections(profile_weights: dict, recommendations: list[Any]) -> dict[str, float]:
    """Return conservative deltas for selected items only.

    Repeated recommendation IDs in one request are deduplicated. Non-selected
    cards are deliberately not inputs and therefore cannot become dislikes.
    The DB layer must additionally deduplicate the request's idempotency key.
    """
    del profile_weights  # current magnitude does not change evidence strength
    adjustments: dict[str, float] = {}
    seen: set[str] = set()
    for recommendation in recommendations:
        key = _recommendation_key(recommendation)
        if key in seen:
            continue
        seen.add(key)
        tags = _recommendation_tags(recommendation)
        if not tags:
            continue
        share = SELECTION_DELTA / len(tags)
        for tag in tags:
            adjustments[tag] = min(MAX_TAG_BATCH_DELTA, adjustments.get(tag, 0.0) + share)
    return adjustments


def learn_from_rating(
    profile_weights: dict,
    selected_recommendations: list[Any],
    rating: int | float,
) -> dict[str, float]:
    """Return rating deltas centered on 3/5 (3 is exactly neutral)."""
    del profile_weights
    rating_value = float(rating)
    if not 1 <= rating_value <= 5:
        raise ValueError("rating must be between 1 and 5")
    amount = (rating_value - 3.0) * RATING_STEP
    if math.isclose(amount, 0.0):
        return {}
    tagged: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for recommendation in selected_recommendations:
        key = _recommendation_key(recommendation)
        if key in seen:
            continue
        seen.add(key)
        tags = _recommendation_tags(recommendation)
        if tags:
            tagged.append((key, tags))
    tag_slots = sum(len(tags) for _, tags in tagged)
    if tag_slots == 0:
        return {}
    share = amount / tag_slots
    adjustments: dict[str, float] = {}
    for _, tags in tagged:
        for tag in tags:
            adjustments[tag] = adjustments.get(tag, 0.0) + share
    return adjustments


def apply_weight_adjustments(profile_weights: dict, adjustments: dict[str, float]) -> dict:
    """Apply one already-deduplicated batch and renormalize the vibe vector."""
    updated = dict(profile_weights)
    current_raw = updated.get("vibe_weights", {})
    current = {vibe: max(0.0, float(current_raw.get(vibe, 0.0))) for vibe in PROFILE_VIBES}
    if sum(current.values()) <= 0:
        current = {vibe: 1.0 / len(PROFILE_VIBES) for vibe in PROFILE_VIBES}
    for vibe, delta in adjustments.items():
        if vibe not in current:
            continue
        bounded = max(-MAX_TAG_BATCH_DELTA, min(MAX_TAG_BATCH_DELTA, float(delta)))
        current[vibe] = max(WEIGHT_FLOOR, current[vibe] + bounded)
    total = sum(current.values())
    updated["vibe_weights"] = {vibe: current[vibe] / total for vibe in PROFILE_VIBES}
    return updated
