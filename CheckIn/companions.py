"""Trip companions: guest sketches saved by the organizer and linked accounts.

This module is the single seam between trip creation, research, and the
profiles of people other than the organizer. Nothing outside it may read
another account's character profile.

Invariant: a linked account's profile is read only while the organizer→member
``companion_links`` row is ``accepted``. Every other state (no row, pending,
declined, revoked) behaves exactly like a companion with no profile at all,
and the organizer never receives the member's sketch text over the API.
"""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

import db
import profiles
from schemas import TripPreferences


class CompanionInviteInput(BaseModel):
    """POST body for /api/companions/links."""
    username: str = Field(..., min_length=1, max_length=80)


def _accepted_member(user_id: str, username: str) -> dict | None:
    member = db.get_user_by_username(username)
    if member is None or db.companion_link_status(user_id, member["user_id"]) != "accepted":
        return None
    return member


def validate_trip_companions(user_id: str, prefs: TripPreferences) -> None:
    """Raise an HTTPException unless every companion may join this trip."""
    for name in prefs.cotravellers:
        if profiles.load_cotraveller(user_id, name) is None:
            raise HTTPException(status_code=400, detail=f"No saved co-traveller named '{name}'")

    own_username = (db.get_user_by_id(user_id) or {}).get("username")
    for username in prefs.cotraveller_usernames:
        if own_username and username == own_username.lower():
            raise HTTPException(
                status_code=400,
                detail="You are already on this trip — add companions other than yourself",
            )
        member = db.get_user_by_username(username)
        if member is None:
            raise HTTPException(status_code=400, detail=f"No CheckIn user named '@{username}'")
        if db.companion_link_status(user_id, member["user_id"]) != "accepted":
            raise HTTPException(
                status_code=403,
                detail=f"@{username} hasn't accepted your travel invitation yet",
            )
        if profiles.load_sketch(member["user_id"]) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"@{username} hasn't completed their taste profile yet — "
                    "ask them to finish onboarding first"
                ),
            )


def guest_companion_profiles(user_id: str, names: list[str]) -> tuple[list[str], list[dict]]:
    """Prose sketches and taste dicts for the organizer's own saved guests."""
    sketches: list[str] = []
    tastes: list[dict] = []
    for name in names:
        raw = profiles.load_cotraveller(user_id, name)
        if raw:
            prose, _ = profiles.parse_taste(raw)
            sketches.append(prose)
        taste = profiles.get_cotraveller_taste(user_id, name)
        if taste:
            tastes.append(taste)
    return sketches, tastes


def linked_companion_profiles(user_id: str, usernames: list[str]) -> tuple[list[str], list[dict]]:
    """Prose sketches and taste dicts for linked accounts whose invitation is accepted."""
    sketches: list[str] = []
    tastes: list[dict] = []
    for username in usernames:
        member = _accepted_member(user_id, username)
        if member is None:
            continue
        raw = profiles.load_sketch(member["user_id"])
        if raw:
            prose, _ = profiles.parse_taste(raw)
            sketches.append(prose)
        taste = profiles.get_taste(member["user_id"])
        if taste:
            tastes.append(taste)
    return sketches, tastes
