"""Taste vector scoring engine - matches recs against what people actually like."""

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


def _observed_trait(rec, positive_terms, negative_terms, metadata_value=None):
    """Infer a 0..1 trait direction from structured metadata and user-facing copy."""
    if metadata_value is not None:
        low = str(metadata_value).lower()
        if low in {"high", "lively", "social", "luxury"}:
            return 0.9
        if low in {"moderate", "balanced", "casual"}:
            return 0.55
        if low in {"low", "quiet", "intimate", "private"}:
            return 0.15
    text = searchable_text(rec)
    positive = any(term in text for term in positive_terms)
    negative = any(term in text for term in negative_terms)
    if positive and not negative:
        return 0.9
    if negative and not positive:
        return 0.1
    return 0.5


def trait_score(rec, taste):
    """Score recommendation metadata/copy against the editable UI trait model."""
    traits = (taste or {}).get("traits")
    if not isinstance(traits, dict) or not traits:
        return (0.5, [])

    observations = {
        "adventureLevel": _observed_trait(
            rec, ["adventure", "hike", "thrill", "backroad", "unusual"],
            ["gentle", "easy", "relaxed"], rec.metadata.get("physical_intensity")
        ),
        "socialPreference": _observed_trait(
            rec, ["lively", "social", "communal", "group", "nightlife"],
            ["quiet", "private", "intimate", "secluded"], rec.metadata.get("vibe")
        ),
        "comfortPreference": _observed_trait(
            rec, ["luxury", "premium", "comfortable", "private transfer", "boutique"],
            ["hostel", "rugged", "basic", "shared room"]
        ),
        "spontaneity": 0.15 if rec.metadata.get("booking_required") is True or rec.metadata.get("reservation_needed") is True else 0.75,
        "localVsTourist": _observed_trait(
            rec, ["local", "neighborhood", "authentic", "insider", "market", "family-run"],
            ["touristy", "tourist", "iconic landmark", "must-see crowds"]
        ),
        "nightlifeInterest": _observed_trait(
            rec, ["night", "bar", "club", "evening", "lively"],
            ["early morning", "dawn", "quiet evening"]
        ),
        "natureVsUrban": _observed_trait(
            rec, ["nature", "forest", "trail", "garden", "mountain", "coast"],
            ["downtown", "city center", "urban", "shopping district"]
        ),
    }
    if rec.category == "restaurant":
        observations["foodAdventurousness"] = _observed_trait(
            rec, ["street food", "tasting", "unusual", "regional", "chef's counter"],
            ["familiar", "international chain", "classic comfort"]
        )

    fits = []
    matched = []
    for key, observed in observations.items():
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

    text = searchable_text(rec)
    matched = []
    violated = []

    likes = taste.get("likes") or {}
    earned = 0
    total_weight = 0
    for like in likes:
        weight = likes[like]
        total_weight += weight
        if any_term_matches(like, text):
            matched.append(like)
            earned += weight

    if total_weight > 0:
        affinity = earned / total_weight
    else:
        # no likes on file, stay neutral
        affinity = 0.5

    score = affinity
    traits, trait_matches = trait_score(rec, taste)
    if isinstance(taste.get("traits"), dict) and taste["traits"]:
        score = 0.7 * affinity + 0.3 * traits
        matched.extend(trait_matches)

    # soft dislikes chip away at the score; strength-3 ones are
    # handled as dealbreakers elsewhere, not here
    dislikes = taste.get("dislikes") or {}
    for dislike in dislikes:
        weight = dislikes[dislike]
        if weight >= 3:
            continue
        if any_term_matches(dislike, text):
            violated.append(dislike)
            score -= (weight / 3) * 0.15

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
    """Hard vetoes: strength-3 dislikes and diet conflicts."""
    if not taste:
        return []

    text = searchable_text(rec)
    dealbreakers = []

    dislikes = taste.get("dislikes") or {}
    for dislike in dislikes:
        if dislikes[dislike] >= 3 and any_term_matches(dislike, text):
            dealbreakers.append("dislikes: " + dislike)

    # only food-ish categories can conflict with a diet;
    # a hotel isn't going to force-feed anyone steak
    if rec.category in ("restaurant", "activity"):
        for diet in taste.get("diet") or []:
            rules = DIET_RULES.get(diet.lower())
            if rules is None:
                continue

            # if the rec explicitly says it caters to this diet, it's cleared
            friendly_tags = rec.metadata.get("dietary_friendly")
            cleared = False
            if isinstance(friendly_tags, list):
                for tag in friendly_tags:
                    if diet.lower() in str(tag).lower():
                        cleared = True
            if cleared:
                continue

            for conflict in rules["conflicts"]:
                if term_matches(conflict, text):
                    dealbreakers.append("not " + diet + "-friendly")
                    break

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
    likes = taste.get("likes") or {}
    dislikes = taste.get("dislikes") or {}
    traits = taste.get("traits") if isinstance(taste.get("traits"), dict) else {}
    return min(1.0, (len(likes) + len(dislikes) + len(traits) * 0.5) / 10)
