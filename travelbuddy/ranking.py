"""Scores and sorts recommendations so we don't rely on the LLM's ordering."""

from __future__ import annotations

from schemas import CURRENCY_TO_USD, Recommendation, TripPreferences
from tastes import group_score, profile_confidence, taste_score

# how much each signal counts toward the final score.
# taste is scaled by profile confidence — a thin profile hands
# its unspent weight to rating so 6 answers never beat 2000 reviews
WEIGHT_BUDGET = 0.30
WEIGHT_TASTE = 0.30
WEIGHT_RATING = 0.25
WEIGHT_VIBES = 0.15

# a dealbreaker doesn't hide the rec, it slams it to the bottom
# with the reason attached so the user can see (and override) it
DEALBREAKER_MULTIPLIER = 0.15

# ratings with few reviews get pulled toward 3.5 so a fake-looking
# 5.0 with no reviews can't beat a solid 4.4 with thousands
PRIOR_RATING = 3.5
PRIOR_REVIEW_WEIGHT = 20

# how the total trip budget roughly splits across spending categories.
# transport gets a big share because it includes getting to the
# destination (flights/trains), not just metro cards
BUDGET_SPLIT = {
    "hotel": 0.35,
    "restaurant": 0.20,
    "activity": 0.15,
    "transport": 0.30,
}


def rating_score(rec: Recommendation) -> float:
    # blend the rating with the 3.5 prior, more reviews = trust it more
    reviews = max(rec.review_count, 0)
    damped = (rec.rating * reviews + PRIOR_RATING * PRIOR_REVIEW_WEIGHT) / (
        reviews + PRIOR_REVIEW_WEIGHT
    )
    return damped / 5.0


def vibe_score(rec: Recommendation, prefs: TripPreferences) -> float:
    # how many of the trip's vibes show up in the rec's text.
    # matching on the first 5 letters so "culture" also hits "cultural".
    # (taste-profile matching moved to tastes.py — this is trip-level only)
    terms = list(prefs.vibes)
    if not terms:
        return 0.5

    searchable = " ".join(
        [rec.name, rec.description, rec.reasoning, str(rec.metadata)]
    ).lower()

    matched = 0
    for term in terms:
        stem = term.lower()
        if len(stem) > 5:
            stem = stem[:5]
        if stem in searchable:
            matched += 1
    return matched / len(terms)


def category_allowance(category: str, prefs: TripPreferences) -> float | None:
    # what this category can afford per unit, worked out from the user's
    # actual budget. units match how each agent prices things:
    # hotels per night, restaurants per person per meal,
    # activities per person, transport as a trip total
    share = BUDGET_SPLIT.get(category)
    if share is None:
        return None

    budget_usd = prefs.budget_amount * CURRENCY_TO_USD[prefs.currency]
    pot = budget_usd * share
    days = (prefs.end_date - prefs.start_date).days + 1
    nights = max(days - 1, 1)

    if category == "hotel":
        return pot / nights
    if category == "restaurant":
        return pot / (days * 3 * prefs.num_travelers)  # 3 meals a day
    if category == "activity":
        return pot / (days * prefs.num_travelers)  # ~1 paid activity per person per day
    return pot  # transport


def budget_score(rec: Recommendation, prefs: TripPreferences) -> float:
    # 1.0 if it fits the allowance, proportional penalty the further
    # over it goes. cheaper than the allowance is just fine.
    # 0.5 = neutral when we have no cost data
    if rec.cost_max <= 0:
        return 0.5

    allowance = category_allowance(rec.category, prefs)
    if allowance is None or allowance <= 0:
        return 0.5

    midpoint = (rec.cost_min + rec.cost_max) / 2
    if midpoint <= allowance:
        return 1.0
    return allowance / midpoint


def score_recommendation(
    rec: Recommendation,
    prefs: TripPreferences,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
) -> dict:
    # combine the four signals into one 0..1 score
    rating = rating_score(rec)
    vibes = vibe_score(rec, prefs)
    budget = budget_score(rec, prefs)

    # taste: group least-misery score, weight scaled by how rich
    # the profile actually is. no profile = old three-signal behavior
    matched = []
    conflicts = []
    taste = 0.5
    confidence = 0.0
    if user_taste:
        result = group_score(rec, user_taste, cotraveller_tastes or [])
        taste = result["score"]
        matched = result["matched"]
        conflicts = result["conflicts"]
        confidence = profile_confidence(user_taste)

    taste_weight = WEIGHT_TASTE * confidence
    rating_weight = WEIGHT_RATING + WEIGHT_TASTE * (1 - confidence)

    total = (
        WEIGHT_BUDGET * budget
        + taste_weight * taste
        + rating_weight * rating
        + WEIGHT_VIBES * vibes
    )

    # any member's dealbreaker slams the score — shown, flagged, ranked last
    if conflicts:
        total = total * DEALBREAKER_MULTIPLIER

    return {
        "rating": round(rating, 3),
        "vibes": round(vibes, 3),
        "budget": round(budget, 3),
        "taste": round(taste, 3),
        "matched": matched,
        "conflicts": conflicts,
        "total": round(total, 3),
    }


def _sort_key(rec: Recommendation) -> tuple:
    # best score first, ties broken by rating then review count
    return (-rec.score, -rec.rating, -rec.review_count)


def rank_recommendations(
    recommendations: list[Recommendation],
    prefs: TripPreferences,
    user_taste: dict | None = None,
    cotraveller_tastes: list[dict] | None = None,
) -> list[Recommendation]:
    """Score everything, sort best-first, and number them (rank 1 = best)."""
    for rec in recommendations:
        breakdown = score_recommendation(rec, prefs, user_taste, cotraveller_tastes)
        rec.score = breakdown["total"]
        rec.score_breakdown = breakdown

    ordered = sorted(recommendations, key=_sort_key)

    position = 1
    for rec in ordered:
        rec.rank = position
        position += 1

    return ordered
