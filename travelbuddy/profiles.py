"""Taste profiles: intake chat, sketch + taste storage, post-trip updates."""

from __future__ import annotations

import json
import logging
import re

import db
from llm_client import generate_text
from prompts.profile_chat import (
    COTRAVELLER_SYSTEM,
    INTAKE_SYSTEM,
    build_cotraveller_sketch_prompt,
    build_sketch_prompt,
)
from prompts.profile_update import build_update_prompt
from schemas import TripPreferences

logger = logging.getLogger(__name__)

USER_QUESTIONS = 6
COTRAVELLER_QUESTIONS = 4

# in-flight intake conversations. key is user_id, or "user_id/slug"
# for a co-traveller chat. lost on restart, which just means
# the chat starts over — fine for a prototype
_chats: dict[str, list[dict]] = {}

INTAKE_DONE_MESSAGE = (
    "That's everything I needed — I've got a solid picture now. Let's plan something great!"
)


# --- storage (SQLite via db.py) ---

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "someone"


def load_sketch(user_id: str) -> str | None:
    row = db.get_profile(user_id, "self")
    if row is None:
        return None
    return row["sketch_md"]


def save_sketch(user_id: str, raw_text: str, raw_answers: list[str] | None = None) -> None:
    # store the raw sketch (with its taste json block) plus the
    # extracted taste separately so the ranker never has to parse md
    _clean, taste = parse_taste(raw_text)
    if taste is not None and raw_answers:
        taste["rawAnswers"] = raw_answers
    taste_json = json.dumps(taste) if taste else None
    db.save_profile(user_id, "self", "self", "self", raw_text.strip(), taste_json)


def _profile_traits(taste: dict | None) -> dict:
    """Return the normalized UI trait model while preserving ranker-friendly taste fields."""
    taste = taste or {}
    supplied = taste.get("traits") if isinstance(taste.get("traits"), dict) else {}
    pace = supplied.get("pace") or taste.get("pace") or "balanced"
    if pace == "moderate":
        pace = "balanced"
    elif pace == "packed":
        pace = "fast"
    defaults = {
        "pace": pace,
        "budgetStyle": "balanced",
        "adventureLevel": 0.55,
        "socialPreference": 0.5,
        "comfortPreference": 0.55,
        "spontaneity": 0.5,
        "localVsTourist": 0.65,
        "foodAdventurousness": 0.6,
        "nightlifeInterest": 0.35,
        "natureVsUrban": 0.5,
    }
    defaults.update(supplied)
    return defaults


def _clean_profile_summary(sketch: str) -> str:
    """Remove markdown storage metadata from the user-facing prose summary."""
    prose, _taste = parse_taste(sketch)
    lines = [
        line for line in prose.splitlines()
        if line.strip().lower() not in {"# character sketch", "character sketch"}
        and not line.strip().lower().startswith("keywords:")
    ]
    return "\n".join(lines).strip()


def get_character_profile(user_id: str) -> dict | None:
    """Return the stable frontend contract for the user's character.md profile."""
    row = db.get_profile(user_id, "self")
    if row is None:
        return None
    _, parsed = parse_taste(row["sketch_md"])
    taste = get_taste(user_id) or parsed or {}
    return {
        "id": f"character:{user_id}",
        "version": 1,
        "summary": _clean_profile_summary(row["sketch_md"]),
        "traits": _profile_traits(taste),
        "rawAnswers": taste.get("rawAnswers", []),
        "createdAt": row["updated_at"],
        "updatedAt": row["updated_at"],
    }


def update_character_profile(user_id: str, summary: str, traits: dict) -> dict:
    """Persist an edited summary and traits without discarding ranking taste signals."""
    current = get_taste(user_id) or {"likes": {}, "dislikes": {}, "diet": [], "pace": "moderate"}
    current["traits"] = traits
    ui_pace = traits.get("pace")
    current["pace"] = {"slow": "slow", "balanced": "moderate", "fast": "packed"}.get(
        str(ui_pace), current.get("pace", "moderate")
    )
    keywords = list(current.get("likes", {}).keys())[:10]
    raw = (
        "# Character Sketch\n"
        + "keywords: " + ", ".join(keywords) + "\n\n"
        + "```json\n" + json.dumps(current, ensure_ascii=False) + "\n```\n\n"
        + summary.strip()
    )
    db.save_profile(user_id, "self", "self", "self", raw, json.dumps(current))
    return get_character_profile(user_id) or {}


def reset_character_profile(user_id: str) -> bool:
    """Clear a saved profile and any in-flight onboarding conversation."""
    _chats.pop(user_id, None)
    return db.delete_profile(user_id, "self", "self")


def load_cotraveller(user_id: str, name: str) -> str | None:
    row = db.get_profile(user_id, "cotraveller", slugify(name))
    if row is None:
        return None
    return row["sketch_md"]


def save_cotraveller(user_id: str, name: str, raw_text: str) -> None:
    _clean, taste = parse_taste(raw_text)
    taste_json = json.dumps(taste) if taste else None
    db.save_profile(user_id, "cotraveller", slugify(name), name, raw_text.strip(), taste_json)


def list_cotravellers(user_id: str) -> list[str]:
    names = []
    for row in db.list_cotraveller_profiles(user_id):
        names.append(row["slug"])
    return names


def parse_keywords(sketch_text: str) -> list[str]:
    """Pull the machine-readable tags off the 'keywords:' line."""
    for line in sketch_text.splitlines():
        if line.strip().lower().startswith("keywords:"):
            raw = line.split(":", 1)[1]
            keywords = []
            for part in raw.split(","):
                part = part.strip()
                if part:
                    keywords.append(part)
            return keywords
    return []


# --- taste vector handling ---

DIET_WORDS = ["vegetarian", "vegan", "halal", "kosher", "gluten-free"]


def _normalize_taste(taste: dict) -> dict:
    # the model sometimes writes "diet": "vegetarian" instead of a list —
    # iterating that string would silently kill the diet veto
    diet = taste.get("diet")
    if isinstance(diet, str):
        if diet.strip():
            taste["diet"] = [diet.strip()]
        else:
            taste["diet"] = []
    if not isinstance(taste.get("likes"), dict):
        taste["likes"] = {}
    if not isinstance(taste.get("dislikes"), dict):
        taste["dislikes"] = {}
    return taste


def parse_taste(raw_text: str) -> tuple[str, dict | None]:
    """Split the fenced ```json taste block out of a sketch.

    Returns (prose without the block, taste dict or None if missing/broken).
    """
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if match is None:
        return raw_text.strip(), None
    taste = None
    try:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, dict):
            taste = _normalize_taste(parsed)
    except Exception:
        taste = None  # broken block, prose is still usable
    clean = raw_text.replace(match.group(0), "").strip()
    return clean, taste


def _taste_from_keywords(sketch_md: str) -> dict | None:
    # migrated pre-DB sketches have no taste json — fake a rough one
    # from the keywords line so they still get some personalization
    keywords = parse_keywords(sketch_md)
    if not keywords:
        return None
    likes = {}
    dislikes = {}
    diet = []
    for kw in keywords:
        low = kw.lower()
        if low in DIET_WORDS:
            diet.append(low)
        elif low.startswith("hates ") or low.startswith("no "):
            term = low.replace("hates ", "", 1).replace("no ", "", 1)
            dislikes[term] = 2  # soft — don't veto off a fuzzy migrated keyword
        else:
            likes[kw] = 2
    return {"likes": likes, "dislikes": dislikes, "diet": diet, "pace": "moderate"}


def get_taste(user_id: str) -> dict | None:
    """The user's taste vector, with a keyword fallback for migrated rows."""
    row = db.get_profile(user_id, "self")
    if row is None:
        return None
    if row["taste_json"]:
        try:
            return _normalize_taste(json.loads(row["taste_json"]))
        except Exception:
            pass
    return _taste_from_keywords(row["sketch_md"])


def get_cotraveller_taste(user_id: str, name: str) -> dict | None:
    row = db.get_profile(user_id, "cotraveller", slugify(name))
    if row is None:
        return None
    if row["taste_json"]:
        try:
            return _normalize_taste(json.loads(row["taste_json"]))
        except Exception:
            pass
    return _taste_from_keywords(row["sketch_md"])


# --- intake chat ---

def _transcript(history: list[dict]) -> str:
    lines = []
    for msg in history:
        if msg["role"] == "assistant":
            lines.append("Buddy: " + msg["content"])
        else:
            lines.append("Traveler: " + msg["content"])
    return "\n".join(lines)


async def chat_turn(user_id: str, message: str, cotraveller_name: str | None = None) -> tuple[str, bool]:
    """One turn of the intake conversation. Returns (reply, done).

    Send an empty message to start the chat and get the first question.
    Once enough answers are in, the sketch gets written to disk and done=True.
    """
    key = user_id
    if cotraveller_name:
        key = user_id + "/" + slugify(cotraveller_name)

    history = _chats.get(key)
    if history is None:
        history = []
        _chats[key] = history

    if message.strip():
        history.append({"role": "user", "content": message.strip()})

    if cotraveller_name:
        needed = COTRAVELLER_QUESTIONS
        system = COTRAVELLER_SYSTEM.replace("{name}", cotraveller_name)
    else:
        needed = USER_QUESTIONS
        system = INTAKE_SYSTEM

    answers = 0
    for msg in history:
        if msg["role"] == "user":
            answers += 1

    if answers >= needed:
        # enough answers — turn the conversation into a sketch
        if cotraveller_name:
            prompt = build_cotraveller_sketch_prompt(_transcript(history), cotraveller_name)
        else:
            prompt = build_sketch_prompt(_transcript(history))
        sketch = await generate_text(prompt, cheap=True, max_output_tokens=600, temperature=0.4)

        if cotraveller_name:
            save_cotraveller(user_id, cotraveller_name, sketch)
        else:
            answers = [msg["content"] for msg in history if msg["role"] == "user"]
            save_sketch(user_id, sketch, answers)
        _chats.pop(key, None)
        return INTAKE_DONE_MESSAGE, True

    # otherwise ask the next question
    reply = await generate_text(
        "Conversation so far:\n" + (_transcript(history) or "(nothing yet — this is the start)")
        + "\n\nReply with your next single message.",
        system_instruction=system,
        cheap=True,
        max_output_tokens=150,
        temperature=0.7,
    )
    reply = reply.strip()
    history.append({"role": "assistant", "content": reply})
    return reply, False


# --- post-trip update ---

async def update_sketch_from_trip(
    user_id: str,
    prefs: TripPreferences,
    picked: list[str],
    skipped: list[str],
) -> None:
    """Revise character.md based on what the traveler picked vs skipped.

    Runs in the background after an itinerary — failure just keeps
    the old sketch, never blocks anything.
    """
    current = load_sketch(user_id)
    if not current:
        return

    trip_summary = "%s to %s, %s to %s, budget %s %s" % (
        prefs.origin, prefs.destination,
        prefs.start_date, prefs.end_date,
        prefs.budget_amount, prefs.currency,
    )

    try:
        revised = await generate_text(
            build_update_prompt(current, trip_summary, picked, skipped),
            cheap=True,
            max_output_tokens=600,
            temperature=0.3,
        )
        if "keywords:" in revised.lower():
            save_sketch(user_id, revised)
            logger.info("Profile updated for %s after trip to %s", user_id, prefs.destination)
        else:
            logger.warning("Profile update came back malformed, keeping old sketch")
    except Exception as exc:
        logger.warning("Profile update failed, keeping old sketch: %s", exc)
