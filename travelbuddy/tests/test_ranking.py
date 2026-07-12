"""Tests for the custom ranking algorithm."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ranking import budget_score, rank_recommendations, rating_score, vibe_score
from schemas import BudgetTier, GroupType, Recommendation, TripPreferences


def make_prefs(budget_tier=BudgetTier.MODERATE, vibes=None):
    if vibes is None:
        vibes = ["food", "culture"]
    return TripPreferences(
        destination="Tokyo",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 7),
        budget_tier=budget_tier,
        vibes=vibes,
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )


def make_rec(**overrides):
    fields = {
        "name": "Test Place",
        "category": "restaurant",
        "description": "A lovely place.",
        "reasoning": "Great fit.",
        "estimated_cost": "$20-$40 per person",
        "cost_min": 20,
        "cost_max": 40,
        "rating": 4.5,
        "review_count": 500,
        "location": "Shibuya, Tokyo",
        "image_search_query": "test place tokyo",
    }
    fields.update(overrides)
    return Recommendation(**fields)


def test_rating_score_damps_unreviewed_ratings():
    # A perfect 5.0 with zero reviews should score below a 4.4 with many reviews
    unverified = make_rec(rating=5.0, review_count=0)
    well_attested = make_rec(rating=4.4, review_count=2000)
    assert rating_score(unverified) < rating_score(well_attested)


def test_vibe_score_counts_matched_vibes():
    prefs = make_prefs(vibes=["food", "culture"])
    both = make_rec(description="A cultural food market experience.")
    one = make_rec(description="A famous food market.")
    neither = make_rec(description="A quiet park.", reasoning="Nice.", name="Park")
    assert vibe_score(both, prefs) == 1.0
    assert vibe_score(one, prefs) == 0.5
    assert vibe_score(neither, prefs) == 0.0


def test_budget_score_penalizes_over_budget():
    prefs = make_prefs(budget_tier=BudgetTier.BUDGET)
    in_band = make_rec(cost_min=10, cost_max=20)       # midpoint 15, band 0-25
    over = make_rec(cost_min=100, cost_max=200)        # midpoint 150, way over
    assert budget_score(in_band, prefs) == 1.0
    assert budget_score(over, prefs) < 0.2


def test_budget_score_penalizes_too_cheap_for_luxury():
    prefs = make_prefs(budget_tier=BudgetTier.LUXURY)
    cheap = make_rec(category="hotel", cost_min=30, cost_max=50)  # below 350 floor
    assert budget_score(cheap, prefs) < 0.5


def test_budget_score_neutral_without_cost_data():
    prefs = make_prefs()
    no_cost = make_rec(cost_min=0, cost_max=0)
    assert budget_score(no_cost, prefs) == 0.5


def test_rank_orders_best_first_and_assigns_ranks():
    prefs = make_prefs(vibes=["food"])
    weak = make_rec(name="Meh Cafe", rating=3.2, review_count=40,
                    description="A cafe.", reasoning="It exists.")
    strong = make_rec(name="Great Izakaya", rating=4.7, review_count=3000,
                      description="Legendary food spot.", reasoning="Perfect food match.")
    ranked = rank_recommendations([weak, strong], prefs)

    assert ranked[0].name == "Great Izakaya"
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    assert ranked[0].score > ranked[1].score
    assert set(ranked[0].score_breakdown.keys()) == {"rating", "vibes", "budget", "total"}


def test_scores_stay_in_unit_range():
    prefs = make_prefs()
    extreme = make_rec(rating=5.0, review_count=10**6, cost_min=1, cost_max=2)
    ranked = rank_recommendations([extreme], prefs)
    assert 0.0 <= ranked[0].score <= 1.0
