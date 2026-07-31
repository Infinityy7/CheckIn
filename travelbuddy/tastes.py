"""Structured profile scoring and hard-constraint checks."""

import math

from personalization import PROFILE_VIBES
from schemas import Recommendation

# travel concepts -> extra words that mean roughly the same thing.
# keeps the matching from being too literal ("temples" should hit "shrine")
SYNONYMS = {
    "temples": ["temple", "shrine", "pagoda", "monastery", "spiritual"],
    "street food": ["food stall", "hawker", "market food", "food cart", "street-side"],
    "crowds": ["crowded", "busy", "packed", "queues", "touristy", "tourist crowds"],
    "photography": ["photo", "scenic", "viewpoint", "instagram"],
    "local markets": ["market", "bazaar", "flea market"],
    "nightlife": ["bar", "club", "night out"],
    "museums": ["museum", "gallery", "exhibit"],
    "nature": ["park", "garden", "forest", "hike", "trail"],
    "beaches": ["beach", "coast", "seaside"],
    "history": ["historic", "heritage", "ancient", "ruins"],
    "luxury": ["upscale", "five-star", "premium"],
    "tourist traps": ["tourist trap", "overpriced", "gimmick"],
    "group tours": ["group tour", "guided group", "tour bus"],
    "quiet": ["peaceful", "tranquil", "serene", "calm"],
    "adventure": ["thrill", "extreme", "adrenaline"],
    "shopping": ["mall", "boutique", "souvenir"],
    "art": ["gallery", "street art", "mural"],
    "architecture": ["building", "cathedral", "palace", "castle"],
    "wine": ["winery", "vineyard", "tasting"],
    "coffee": ["cafe", "espresso", "roastery"],
    "live music": ["concert", "jazz", "gig"],
    "festivals": ["festival", "matsuri", "celebration"],
    "views": ["viewpoint", "panorama", "skyline", "rooftop"],
    "wellness": ["spa", "onsen", "massage", "yoga"],
    "cycling": ["bike", "bicycle"],
    "walking": ["stroll", "walkable", "on foot"],
    "seafood": ["fish market", "sushi", "oyster"],
    "vegetarian food": ["vegetarian", "plant-based", "veggie"],
    "theme parks": ["amusement park", "disney", "universal"],
    "day trips": ["day trip", "excursion"],
}

# per-diet hard conflicts, plus nothing fancy for "friendly" -
# that gets checked against rec metadata instead
DIET_RULES = {
    "vegetarian": {
        "conflicts": ["steakhouse", "steak", "yakiniku", "yakitori", "bbq",
                      "barbecue", "butcher", "meat-focused", "pork", "seafood-only"],
        "friendly": ["vegetarian"],
    },
    "vegan": {
        "conflicts": ["steakhouse", "steak", "yakiniku", "yakitori", "bbq",
                      "barbecue", "butcher", "meat-focused", "pork", "seafood-only",
                      "cheese tasting", "dairy"],
        "friendly": ["vegan"],
    },
    "halal": {
        "conflicts": ["pork", "pub crawl", "wine tasting", "brewery"],
        "friendly": ["halal"],
    },
    "kosher": {
        "conflicts": ["pork", "shellfish"],
        "friendly": ["kosher"],
    },
    "gluten-free": {
        "conflicts": ["pasta making", "bakery crawl", "brewery"],
        "friendly": ["gluten-free", "gluten free"],
    },
}


def expand_terms(term):
    # the term itself plus any synonyms we know about
    return [term] + SYNONYMS.get(term.lower(), [])


def searchable_text(rec):
    # everything we can match against, mashed together
    return " ".join([rec.name, rec.description, rec.reasoning, str(rec.metadata)]).lower()


def term_matches(term, text):
    low = term.lower()
    # multi-word terms get one shot at the full phrase first
    if " " in low and low in text:
        return True
    # otherwise stem it down so "temples" still hits "temple"
    if len(low) > 5:
        stem = low[:5]
    else:
        stem = low
    return stem in text


def any_term_matches(term, text):
    # check the term and all its synonyms
    for candidate in expand_terms(term):
        if term_matches(candidate, text):
            return True
    return False


def _observed_trait(metadata_value=None):
    """Normalize an explicitly structured metadata trait to 0..1."""
    if metadata_value is not None:
        if isinstance(metadata_value, (int, float)):
            return max(0.0, min(1.0, float(metadata_value)))
        low = str(metadata_value).lower()
        if low in {"high", "lively", "social", "luxury"}:
            return 0.9
        if low in {"moderate", "balanced", "casual"}:
            return 0.55
        if low in {"low", "quiet", "intimate", "private"}:
            return 0.15
    return None


def trait_score(rec, taste):
    """Score explicitly structured recommendation metadata against UI traits."""
    traits = (taste or {}).get("traits")
    if not isinstance(traits, dict) or not traits:
        return (0.5, [])

    booking_known = any(
        key in rec.metadata for key in ("booking_required", "reservation_needed")
    )
    observations = {
        "adventureLevel": _observed_trait(rec.metadata.get("adventure_level", rec.metadata.get("physical_intensity"))),
        "socialPreference": _observed_trait(rec.metadata.get("social_level")),
        "comfortPreference": _observed_trait(rec.metadata.get("comfort_level")),
        "spontaneity": (
            0.15 if rec.metadata.get("booking_required") is True or rec.metadata.get("reservation_needed") is True
            else 0.75 if booking_known else None
        ),
        "localVsTourist": _observed_trait(rec.metadata.get("locality_level")),
        "nightlifeInterest": _observed_trait(rec.metadata.get("nightlife_level")),
        "natureVsUrban": _observed_trait(rec.metadata.get("nature_level")),
    }
    if rec.category == "restaurant":
        observations["foodAdventurousness"] = _observed_trait(rec.metadata.get("food_adventurousness"))

    fits = []
    matched = []
    for key, observed in observations.items():
        if observed is None:
            continue
        value = traits.get(key)
        if not isinstance(value, (int, float)):
            continue
        fit = 1.0 - abs(max(0.0, min(1.0, float(value))) - observed)
        fits.append(fit)
        if fit >= 0.75:
            matched.append("trait: " + key)
    if not fits:
        return (0.5, [])
    return (sum(fits) / len(fits), matched)


def taste_score(rec, taste):
    """Score a rec against one person's taste vector. Returns (score, matched, violated)."""
    if not taste:
        # nothing to go on, everyone gets a shrug
        return (0.5, [], [])

    matched = []
    violated = []

    vibe_weights = taste.get("vibe_weights") if isinstance(taste.get("vibe_weights"), dict) else None
    if vibe_weights is None:
        # Migration compatibility: only controlled vibe names survive. We never
        # search generated names/descriptions/reasoning for profile keywords.
        likes = taste.get("likes") or {}
        vibe_weights = {
            vibe: max(0.0, float(likes.get(vibe, 0.0))) for vibe in PROFILE_VIBES
        }
    rec_tags = [tag for tag in rec.vibe_tags if tag in PROFILE_VIBES]
    if rec_tags and sum(vibe_weights.values()) > 0:
        item_value = 1.0 / len(rec_tags)
        dot = sum(float(vibe_weights.get(tag, 0.0)) * item_value for tag in rec_tags)
        profile_norm = math.sqrt(sum(float(vibe_weights.get(vibe, 0.0)) ** 2 for vibe in PROFILE_VIBES))
        item_norm = math.sqrt(len(rec_tags) * item_value ** 2)
        affinity = dot / (profile_norm * item_norm) if profile_norm and item_norm else 0.5
        matched.extend(tag for tag in rec_tags if float(vibe_weights.get(tag, 0.0)) > 0)
    else:
        affinity = 0.5

    score = affinity
    traits, trait_matches = trait_score(rec, taste)
    if isinstance(taste.get("traits"), dict) and taste["traits"]:
        score = 0.7 * affinity + 0.3 * traits
        matched.extend(trait_matches)

    # soft dislikes chip away at the score; strength-3 ones are
    # handled as dealbreakers elsewhere, not here
    # New profiles model no-gos as hard controlled constraints. Legacy fuzzy
    # dislikes are retained in storage but are not scored from generated prose.

    # pace mismatch: slow traveller + intense activity is a bad time
    pace = taste.get("pace")
    intensity = rec.metadata.get("physical_intensity")
    if pace == "slow" and intensity == "high":
        score -= 0.15
        violated.append("pace mismatch")
    if pace == "packed" and intensity == "low":
        score -= 0.05

    # keep it in 0..1
    if score < 0:
        score = 0.0
    if score > 1:
        score = 1.0

    return (score, matched, violated)


def find_dealbreakers(rec, taste):
    """Hard vetoes from controlled candidate fields only."""
    if not taste:
        return []

    dealbreakers = []
    candidate_constraints = set(rec.constraint_tags) | set(rec.dealbreaker_tags)
    for constraint in taste.get("dealbreakers") or []:
        if constraint in candidate_constraints:
            dealbreakers.append("constraint: " + constraint)

    if taste.get("default_party") == "family_young_kids" and "kid_unfriendly" in candidate_constraints:
        dealbreakers.append("constraint: kid_unfriendly")
    if taste.get("default_party") == "multi_generation" and "group_unfriendly" in candidate_constraints:
        dealbreakers.append("constraint: group_unfriendly")

    requirements = set(taste.get("dietary_requirements") or taste.get("diet") or [])
    supported = set(rec.dietary_tags) | set(rec.dietary_accommodations)
    explicit_conflicts = requirements & set(rec.dietary_conflicts)
    for diet in sorted(explicit_conflicts):
        dealbreakers.append("dietary conflict: " + diet)

    serves_food = rec.category == "restaurant" or rec.metadata.get("serves_food") is True
    if serves_food:
        for diet in sorted(requirements - supported - explicit_conflicts):
            dealbreakers.append("dietary compatibility unverified: " + diet)

    return dealbreakers


def group_score(rec, user_taste, cotraveller_tastes):
    """Least-misery group scoring: the user leads but nobody's veto gets ignored."""
    user_score, user_matched, _ = taste_score(rec, user_taste)

    co_scores = []
    co_matched = []
    co_conflicts = []
    for co_taste in cotraveller_tastes:
        co_score, matched, _ = taste_score(rec, co_taste)
        co_scores.append(co_score)
        for term in matched:
            co_matched.append(term)
        for conflict in find_dealbreakers(rec, co_taste):
            co_conflicts.append("co-traveller: " + conflict)

    if co_scores:
        mean_co = sum(co_scores) / len(co_scores)
        affinity = 0.6 * user_score + 0.4 * mean_co
    else:
        affinity = user_score

    # merge everyone's matches, keeping first-seen order
    matched = []
    for term in user_matched + co_matched:
        if term not in matched:
            matched.append(term)

    conflicts = []
    for conflict in find_dealbreakers(rec, user_taste) + co_conflicts:
        if conflict not in conflicts:
            conflicts.append(conflict)

    return {"score": affinity, "matched": matched, "conflicts": conflicts}


def profile_confidence(taste):
    """How much do we actually know about this person? 0..1."""
    if not taste:
        return 0.0
    if isinstance(taste.get("vibe_weights"), dict):
        # A completed nine-question profile is a strong prior, but still leaves
        # room for verified rating/review quality in the ranker.
        return 0.75
    likes = taste.get("likes") or {}
    dislikes = taste.get("dislikes") or {}
    traits = taste.get("traits") if isinstance(taste.get("traits"), dict) else {}
    return min(1.0, (len(likes) + len(dislikes) + len(traits) * 0.5) / 10)
