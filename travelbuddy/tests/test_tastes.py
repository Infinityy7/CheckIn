"""Tests for the taste vector scoring engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from schemas import Recommendation
from tastes import (
    find_dealbreakers,
    group_score,
    profile_confidence,
    taste_score,
    trait_score,
)


def make_rec(**overrides):
    fields = {
        "name": "Test Place",
        "category": "restaurant",
        "description": "A lovely place.",
        "reasoning": "Great fit.",
        "estimated_cost": "$20-$40",
        "cost_min": 20,
        "cost_max": 40,
        "rating": 4.5,
        "review_count": 500,
        "location": "Shibuya, Tokyo",
        "image_search_query": "test place tokyo",
    }
    fields.update(overrides)
    return Recommendation(**fields)


def test_weighted_like_matching():
    rec = make_rec(vibe_tags=["culture"])
    heavy_match = {"vibe_weights": {"culture": .8, "nightlife": .2}}
    light_match = {"vibe_weights": {"culture": .2, "nightlife": .8}}
    heavy_score, _, _ = taste_score(rec, heavy_match)
    light_score, _, _ = taste_score(rec, light_match)
    assert heavy_score > light_score


def test_generated_prose_is_not_a_scoring_signal():
    rec = make_rec(category="activity", description="Culture culture culture", vibe_tags=[])
    score, matched, _ = taste_score(rec, {"vibe_weights": {"culture": 1.0}})
    assert matched == []
    assert score == 0.5


def test_legacy_fuzzy_dislike_does_not_score_generated_prose():
    rec = make_rec(description="A crowded but charming food market.", vibe_tags=["food"])
    clean = {"vibe_weights": {"food": 1.0}}
    with_dislike = {"vibe_weights": {"food": 1.0}, "dislikes": {"crowds": 2}}
    assert taste_score(rec, clean)[0] == taste_score(rec, with_dislike)[0]


def test_strength_three_dislike_is_a_dealbreaker():
    rec = make_rec(constraint_tags=["crowded_spots"])
    taste = {"dealbreakers": ["crowded_spots"]}
    dealbreakers = find_dealbreakers(rec, taste)
    assert "constraint: crowded_spots" in dealbreakers


def test_vegetarian_vs_steakhouse_is_a_dealbreaker():
    rec = make_rec(name="Famous Steakhouse", dietary_conflicts=["vegetarian"])
    taste = {"dietary_requirements": ["vegetarian"]}
    dealbreakers = find_dealbreakers(rec, taste)
    assert "dietary conflict: vegetarian" in dealbreakers


def test_dietary_friendly_metadata_clears_the_dealbreaker():
    rec = make_rec(
        name="Famous Steakhouse",
        description="The best aged beef in town.",
        dietary_tags=["vegetarian"],
    )
    taste = {"dietary_requirements": ["vegetarian"]}
    assert find_dealbreakers(rec, taste) == []


def test_hotels_never_get_diet_dealbreakers():
    # hotel named after a steakhouse, still fine to sleep there
    rec = make_rec(category="hotel", name="The Steakhouse Hotel",
                   description="Rooms above a famous steakhouse.")
    taste = {"diet": ["vegetarian"]}
    assert find_dealbreakers(rec, taste) == []


def test_group_least_misery_cotraveller_veto():
    # user loves nightlife, cotraveller hard-vetoes it
    rec = make_rec(category="activity", name="Golden Gai Bar Crawl",
                   vibe_tags=["nightlife"], constraint_tags=["crowded_spots"])
    user = {"vibe_weights": {"nightlife": 1.0}}
    cotraveller = {"dealbreakers": ["crowded_spots"]}
    result = group_score(rec, user, [cotraveller])
    assert "nightlife" in result["matched"]
    assert "co-traveller: constraint: crowded_spots" in result["conflicts"]


def test_group_score_blends_user_and_cotravellers():
    rec = make_rec(category="activity", vibe_tags=["culture"])
    user = {"vibe_weights": {"culture": 1.0}}
    cotraveller = {"vibe_weights": {"nightlife": 1.0}}
    result = group_score(rec, user, [cotraveller])
    # 0.6 * 1.0 + 0.4 * 0.0 = 0.6
    assert abs(result["score"] - 0.6) < 1e-9
    # no cotravellers means the user's score passes straight through
    solo = group_score(rec, user, [])
    assert solo["score"] == 1.0


def test_profile_confidence_scaling():
    assert profile_confidence(None) == 0.0
    assert profile_confidence({}) == 0.0
    small = {"likes": {"temples": 2}, "dislikes": {"crowds": 1}}
    assert profile_confidence(small) == 0.2
    big = {
        "likes": {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1, "f": 1},
        "dislikes": {"g": 1, "h": 1, "i": 1, "j": 1, "k": 1},
    }
    assert profile_confidence(big) == 1.0


def test_empty_taste_is_neutral():
    rec = make_rec()
    assert taste_score(rec, {}) == (0.5, [], [])
    assert taste_score(rec, None) == (0.5, [], [])
    assert find_dealbreakers(rec, {}) == []


def test_editable_traits_change_recommendation_fit():
    local_walk = make_rec(
        category="activity",
        name="Quiet Neighborhood Backroads",
        description="A local, authentic garden walk through a residential neighborhood.",
        metadata={"physical_intensity": "moderate", "booking_required": False, "locality_level": "high"},
    )
    local_taste = {"traits": {"localVsTourist": 0.95, "spontaneity": 0.9}}
    iconic_taste = {"traits": {"localVsTourist": 0.05, "spontaneity": 0.1}}
    local_score, matched = trait_score(local_walk, local_taste)
    iconic_score, _ = trait_score(local_walk, iconic_taste)
    assert local_score > iconic_score
    assert "trait: localVsTourist" in matched


def test_traits_contribute_to_profile_confidence():
    assert profile_confidence({"traits": {"adventureLevel": 0.8, "localVsTourist": 0.9}}) == 0.1
