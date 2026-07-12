"""Scores and sorts recommendations so we don't rely on the LLM's ordering."""

from __future__ import annotations

from schemas import Recommendation, TripPreferences

# how much each signal counts toward the final score
WEIGHT_RATING = 0.45
WEIGHT_VIBES = 0.35
WEIGHT_BUDGET = 0.20

# ratings with few reviews get pulled toward 3.5 so a fake-looking
# 5.0 with no reviews can't beat a solid 4.4 with thousands
PRIOR_RATING = 3.5
PRIOR_REVIEW_WEIGHT = 20

# rough price ranges (min USD, max USD) for each budget tier.
# hotels = per night, restaurants/activities = per person, transport = per day.
# just guesses, tune these if rankings feel off
BUDGET_BANDS = {
    "hotel": {
        "budget": (0, 120),
        "moderate": (60, 280),
        "premium": (180, 600),
        "luxury": (350, 100000),
    },
    "restaurant": {
        "budget": (0, 25),
        "moderate": (15, 70),
        "premium": (50, 180),
        "luxury": (100, 100000),
    },
    "activity": {
        "budget": (0, 40),
        "moderate": (15, 120),
        "premium": (60, 300),
        "luxury": (150, 100000),
    },
    "transport": {
        "budget": (0, 25),
        "moderate": (10, 80),
        "premium": (40, 200),
        "luxury": (100, 100000),
    },
}


def rating_score(rec: Recommendation) -> float:
    # blend the rating with the 3.5 prior, more reviews = trust it more
    reviews = max(rec.review_count, 0)
    damped = (rec.rating * reviews + PRIOR_RATING * PRIOR_REVIEW_WEIGHT) / (
        reviews + PRIOR_REVIEW_WEIGHT
    )
    return damped / 5.0


def vibe_score(rec: Recommendation, prefs: TripPreferences) -> float:
    # how many of the user's vibes show up in the rec's text.
    # match on the first 5 letters so "culture" also hits "cultural"
    if not prefs.vibes:
        return 0.5

    searchable = " ".join(
        [rec.name, rec.description, rec.reasoning, str(rec.metadata)]
    ).lower()

    matched = 0
    for vibe in prefs.vibes:
        stem = vibe.lower()
        if len(stem) > 5:
            stem = stem[:5]
        if stem in searchable:
            matched += 1
    return matched / len(prefs.vibes)


def budget_score(rec: Recommendation, prefs: TripPreferences) -> float:
    # 1.0 if the price sits inside the band for this tier, less the
    # further it drifts above (too pricey) or below (too cheap for luxury).
    # 0.5 = neutral when we have no cost data
    if rec.cost_max <= 0:
        return 0.5

    bands_for_category = BUDGET_BANDS.get(rec.category)
    if bands_for_category is None:
        return 0.5

    band = bands_for_category.get(prefs.budget_tier.value)
    if band is None:
        return 0.5

    low, high = band
    midpoint = (rec.cost_min + rec.cost_max) / 2

    if midpoint > high:
        return high / midpoint
    if midpoint < low:
        if low <= 0:
            return 1.0
        return max(midpoint, 1.0) / low
    return 1.0


def score_recommendation(rec: Recommendation, prefs: TripPreferences) -> dict:
    # combine the three signals into one 0..1 score
    rating = rating_score(rec)
    vibes = vibe_score(rec, prefs)
    budget = budget_score(rec, prefs)
    total = WEIGHT_RATING * rating + WEIGHT_VIBES * vibes + WEIGHT_BUDGET * budget
    return {
        "rating": round(rating, 3),
        "vibes": round(vibes, 3),
        "budget": round(budget, 3),
        "total": round(total, 3),
    }


def _sort_key(rec: Recommendation) -> tuple:
    # best score first, ties broken by rating then review count
    return (-rec.score, -rec.rating, -rec.review_count)


def rank_recommendations(
    recommendations: list[Recommendation],
    prefs: TripPreferences,
) -> list[Recommendation]:
    """Score everything, sort best-first, and number them (rank 1 = best)."""
    for rec in recommendations:
        breakdown = score_recommendation(rec, prefs)
        rec.score = breakdown["total"]
        rec.score_breakdown = breakdown

    ordered = sorted(recommendations, key=_sort_key)

    position = 1
    for rec in ordered:
        rec.rank = position
        position += 1

    return ordered
