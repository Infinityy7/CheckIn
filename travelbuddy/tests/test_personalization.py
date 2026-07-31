"""Nine-question compilation and conservative learning tests."""

import asyncio
import math

import pytest

import db
import profiles
from personalization import (
    PROFILE_VIBES,
    apply_weight_adjustments,
    compile_questionnaire,
    learn_from_rating,
    learn_from_selections,
    questionnaire_from_saved_answers,
    validate_saved_answer,
)
from schemas import CharacterProfileUpdate, Recommendation, RecommendationFeedbackInput


def answers(**overrides):
    result = {
        "spontaneity": 0.8,
        "top_vibes": ["nature", "relaxation", "romance"],
        "spend_preferences": {"splurge": "experiences", "save": "shopping"},
        "chronotype": "late",
        "archetype": "slow_traveler",
        "default_party": "partner",
        "food_adventurousness": 0.7,
        "constraints": ["theme_parks", "vegetarian"],
        "perfect_moment": "A quiet sunrise followed by breakfast.",
    }
    result.update(overrides)
    return result


def recommendation(rec_id, tags):
    return Recommendation(
        id=rec_id,
        name=rec_id,
        category="activity",
        description="Generated prose is irrelevant.",
        reasoning="Generated reasoning is irrelevant.",
        estimated_cost="$20",
        cost_min=20,
        cost_max=20,
        rating=4.5,
        review_count=200,
        location="Central",
        image_search_query="placeholder",
        vibe_tags=tags,
    )


def test_exact_questionnaire_compiles_two_separate_artifacts():
    parsed = questionnaire_from_saved_answers(answers())
    artifacts = compile_questionnaire(parsed)
    weights = artifacts.weights.model_dump(mode="json")

    assert math.isclose(sum(weights["vibe_weights"].values()), 1.0)
    assert set(weights["vibe_weights"]) == set(PROFILE_VIBES)
    assert weights["spontaneity"] == 0.8
    assert weights["dealbreakers"] == ["theme_parks"]
    assert weights["dietary_requirements"] == ["vegetarian"]
    assert "quiet sunrise" in artifacts.character_md.lower()
    assert "vibe_weights" not in artifacts.character_md


def test_questionnaire_enforces_three_vibes_and_distinct_spend_categories():
    with pytest.raises(ValueError):
        questionnaire_from_saved_answers(answers(top_vibes=["food", "culture"]))
    with pytest.raises(ValueError):
        validate_saved_answer(
            "spend_preferences", {"splurge": "food", "save": "food"}
        )


def test_learning_uses_selections_not_non_selections_and_deduplicates_ids():
    weights = compile_questionnaire(questionnaire_from_saved_answers(answers())).weights.model_dump(mode="json")
    food = recommendation("food-1", ["food", "culture"])
    skipped = recommendation("skip-1", ["nightlife"])

    adjustment = learn_from_selections(weights, [food, food])
    assert adjustment == {"food": 0.006, "culture": 0.006}
    assert "nightlife" not in adjustment
    # A non-selection is not accepted by the API at all; only selected items
    # passed to the function can contribute evidence.
    assert skipped.id != food.id


def test_rating_is_centered_on_three_and_conservative():
    weights = compile_questionnaire(questionnaire_from_saved_answers(answers())).weights.model_dump(mode="json")
    item = recommendation("food-1", ["food"])
    assert learn_from_rating(weights, [item], 3) == {}
    assert learn_from_rating(weights, [item], 5)["food"] == pytest.approx(0.024)
    assert learn_from_rating(weights, [item], 1)["food"] == pytest.approx(-0.024)


def test_adjustments_can_add_a_new_vibe_and_always_renormalize():
    weights = compile_questionnaire(questionnaire_from_saved_answers(answers())).weights.model_dump(mode="json")
    before = weights["vibe_weights"]["food"]
    updated = apply_weight_adjustments(weights, {"food": 0.02})
    assert updated["vibe_weights"]["food"] > before
    assert sum(updated["vibe_weights"].values()) == pytest.approx(1.0)


def test_intake_draft_is_durable_and_completion_returns_frontend_contract(tmp_path, monkeypatch):
    db.DB_PATH = tmp_path / "intake.db"
    db._conn = None
    db.init_db()

    first = profiles.get_intake_state("intake-user")
    assert first["currentQuestion"]["id"] == "spontaneity"
    assert first["total"] == 9

    for question_id, value in answers().items():
        profiles.save_intake_answer("intake-user", question_id, value)

    ready = profiles.get_intake_state("intake-user")
    assert ready["status"] == "ready_to_complete"
    assert ready["currentQuestion"] is None

    polish_calls = 0

    async def polish(*_args, **_kwargs):
        nonlocal polish_calls
        polish_calls += 1
        return "A polished but fact-preserving travel character."

    monkeypatch.setattr(profiles, "generate_text", polish)
    profile = asyncio.run(profiles.complete_intake("intake-user"))
    assert profile["characterMd"].startswith("# Character Sketch")
    assert profile["weights"]["schemaVersion"] == 1
    assert profile["weights"]["spontaneity"] == 0.8
    assert profile["weights"]["dealBreakers"] == ["theme_parks"]
    assert profile["rawAnswers"]["top_vibes"] == ["nature", "relaxation", "romance"]

    retried = asyncio.run(profiles.complete_intake("intake-user"))
    assert retried["version"] == profile["version"]
    assert polish_calls == 1

    # A new connection still sees the completed intake/profile.
    db._conn = None
    resumed = profiles.get_intake_state("intake-user")
    assert resumed["status"] == "complete"
    assert resumed["profile"]["version"] == profile["version"]


def test_profile_update_omitted_traits_remain_none():
    body = CharacterProfileUpdate(summary="A sufficiently detailed travel summary for saving.")
    assert body.traits is None


def test_feedback_contract_uses_server_resolvable_ids():
    body = RecommendationFeedbackInput(
        trip_id="trip-1", recommendation_id="rec-1", sentiment="dislike"
    )
    assert body.trip_id == "trip-1"
    assert body.recommendation_id == "rec-1"
    with pytest.raises(Exception):
        RecommendationFeedbackInput(
            recommendation_name="Generated Name", category="activity", sentiment="dislike"
        )
