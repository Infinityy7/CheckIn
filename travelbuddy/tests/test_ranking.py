"""Tests for the custom ranking algorithm."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ranking import (
    budget_score,
    category_allowance,
    rank_recommendations,
    rating_score,
    vibe_score,
)
from schemas import GroupType, Recommendation, TripPreferences


def make_prefs(budget_amount=2000, currency="USD", vibes=None, num_travelers=2):
    if vibes is None:
        vibes = ["food", "culture"]
    return TripPreferences(
        destination="Tokyo",
        origin="Mumbai",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 5),  # 5 days, 4 nights
        budget_amount=budget_amount,
        currency=currency,
        vibes=vibes,
        group_type=GroupType.COUPLE,
        num_travelers=num_travelers,
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


def test_category_allowance_uses_real_budget():
    # $2000 trip, 5 days, 4 nights, 2 people
    prefs = make_prefs(budget_amount=2000)
    # hotel: 35% of 2000 = 700 over 4 nights = 175/night
    assert category_allowance("hotel", prefs) == 175
    # restaurant: 20% of 2000 = 400 over 5 days * 3 meals * 2 people = ~13.33/meal
    assert round(category_allowance("restaurant", prefs), 2) == 13.33
    # transport: 30% of 2000 = 600 for the whole trip incl. getting there
    assert category_allowance("transport", prefs) == 600


def test_budget_score_penalizes_over_allowance():
    prefs = make_prefs(budget_amount=500)  # tight budget
    # hotel allowance: 35% of 500 / 4 nights = 43.75/night
    cheap = make_rec(category="hotel", cost_min=30, cost_max=50)   # midpoint 40, fits
    pricey = make_rec(category="hotel", cost_min=155, cost_max=195)  # midpoint 175, 4x over
    assert budget_score(cheap, prefs) == 1.0
    assert budget_score(pricey, prefs) == 0.25


def test_budget_score_cheap_is_fine():
    # under the allowance is never penalized
    prefs = make_prefs(budget_amount=10000)
    very_cheap = make_rec(category="hotel", cost_min=20, cost_max=40)
    assert budget_score(very_cheap, prefs) == 1.0


def test_budget_score_converts_currency():
    # same hotel, budget stated in INR: 100000 INR ~= 1200 USD
    prefs_inr = make_prefs(budget_amount=100000, currency="INR")
    # hotel allowance: 35% of 1200 / 4 nights = 105/night
    fits = make_rec(category="hotel", cost_min=90, cost_max=110)    # midpoint 100
    over = make_rec(category="hotel", cost_min=300, cost_max=340)   # midpoint 320
    assert budget_score(fits, prefs_inr) == 1.0
    assert budget_score(over, prefs_inr) < 0.5


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
    assert set(ranked[0].score_breakdown.keys()) == {
        "rating", "vibes", "budget", "taste", "matched", "conflicts", "total",
    }


def test_scores_stay_in_unit_range():
    prefs = make_prefs()
    extreme = make_rec(rating=5.0, review_count=10**6, cost_min=1, cost_max=2)
    ranked = rank_recommendations([extreme], prefs)
    assert 0.0 <= ranked[0].score <= 1.0
