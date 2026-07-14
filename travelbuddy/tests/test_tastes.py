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
    # same likes, but the rec only mentions one of them -
    # matching the heavy like should score higher than matching the light one
    rec = make_rec(description="A serene hilltop shrine with a beautiful garden.")
    heavy_match = {"likes": {"temples": 3, "nightlife": 1}}
    light_match = {"likes": {"temples": 1, "nightlife": 3}}
    heavy_score, _, _ = taste_score(rec, heavy_match)
    light_score, _, _ = taste_score(rec, light_match)
    assert heavy_score > light_score


def test_synonym_expansion_matches_shrine_for_temples():
    rec = make_rec(category="activity", description="Visit the ancient shrine at dawn.")
    score, matched, _ = taste_score(rec, {"likes": {"temples": 2}})
    assert "temples" in matched
    assert score == 1.0


def test_soft_dislike_subtracts_from_score():
    rec = make_rec(description="A crowded but charming food market.")
    clean = {"likes": {"street food": 2}}
    with_dislike = {"likes": {"street food": 2}, "dislikes": {"crowds": 2}}
    clean_score, _, _ = taste_score(rec, clean)
    penalized_score, _, violated = taste_score(rec, with_dislike)
    assert penalized_score < clean_score
    assert "crowds" in violated


def test_strength_three_dislike_is_a_dealbreaker():
    rec = make_rec(description="Very touristy spot, always packed with queues.")
    taste = {"dislikes": {"crowds": 3}}
    dealbreakers = find_dealbreakers(rec, taste)
    assert "dislikes: crowds" in dealbreakers


def test_vegetarian_vs_steakhouse_is_a_dealbreaker():
    rec = make_rec(name="Famous Steakhouse", description="The best aged beef in town.")
    taste = {"diet": ["vegetarian"]}
    dealbreakers = find_dealbreakers(rec, taste)
    assert "not vegetarian-friendly" in dealbreakers


def test_dietary_friendly_metadata_clears_the_dealbreaker():
    rec = make_rec(
        name="Famous Steakhouse",
        description="The best aged beef in town.",
        metadata={"dietary_friendly": ["vegetarian options"]},
    )
    taste = {"diet": ["vegetarian"]}
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
                   description="Hop between tiny bars all night.")
    user = {"likes": {"nightlife": 3}}
    cotraveller = {"dislikes": {"nightlife": 3}}
    result = group_score(rec, user, [cotraveller])
    assert "nightlife" in result["matched"]
    assert "co-traveller: dislikes: nightlife" in result["conflicts"]


def test_group_score_blends_user_and_cotravellers():
    rec = make_rec(category="activity", description="A peaceful temple garden walk.")
    user = {"likes": {"temples": 2}}          # full match -> 1.0
    cotraveller = {"likes": {"nightlife": 2}}  # no match -> 0.0
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
