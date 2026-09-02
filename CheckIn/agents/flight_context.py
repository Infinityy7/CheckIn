"""Supplier-backed flight briefing for the transport agent.

Long-haul fare research is what blew the transport agent's deadlines, so the
flight facts come from the inventory provider instead of web search. The
lookup is strictly best-effort: any provider problem degrades the agent to
labelled estimates rather than failing the category.
"""

from __future__ import annotations

import asyncio
import logging

from config import FLIGHT_BRIEFING_TIMEOUT_SECONDS
from inventory.models import FlightInventory, FlightOffer, SourceMode
from inventory.service import get_inventory_service
from schemas import TripPreferences

logger = logging.getLogger(__name__)

# more offers than the cart shows: the agent wants variety across strategies
BRIEFING_OFFER_LIMIT = 6

# demo inventory is deterministic sample data; feeding it to the agent as
# planning facts would launder fake prices into recommendations
_BRIEFABLE_MODES = {SourceMode.LIVE, SourceMode.TEST}


def briefing_expected() -> bool:
    """True when the configured provider should be able to supply flight facts."""
    provider = get_inventory_service().provider
    return getattr(provider, "source_mode", SourceMode.UNAVAILABLE) in _BRIEFABLE_MODES


async def build_flight_briefing(prefs: TripPreferences) -> str | None:
    """Fetch supplier flight offers and format them for the agent prompt."""
    if not briefing_expected():
        return None
    provider = get_inventory_service().provider
    try:
        async with asyncio.timeout(FLIGHT_BRIEFING_TIMEOUT_SECONDS):
            inventory = await provider.search_flight_inventory(
                recommendation_id="transport-briefing",
                origin=prefs.origin,
                destination=prefs.destination,
                departure_date=prefs.start_date,
                return_date=prefs.end_date,
                adults=prefs.num_travelers,
                limit=BRIEFING_OFFER_LIMIT,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Flight briefing unavailable; transport agent will use labelled "
            "estimates error=%s",
            type(exc).__name__,
        )
        return None
    if not inventory.offers:
        return None
    return format_flight_briefing(inventory, adults=prefs.num_travelers)


def format_flight_briefing(inventory: FlightInventory, *, adults: int) -> str:
    mode = "live bookable" if inventory.is_live else "supplier test-mode"
    lines = [
        "## Supplier flight offers (CheckIn inventory API)",
        (
            f"These are {mode} offers for the requested route and dates, priced "
            f"for the whole party of {adults}. Use them as the flight facts in "
            "your strategies instead of searching the web for fares. Each offer "
            "lists its outbound leg and then its return leg; a return leg marked "
            "unknown has no supplier data and must not be invented."
        ),
    ]
    for offer in inventory.offers:
        flight_number = f" {offer.flight_number}" if offer.flight_number else ""
        journey = offer.journey_type.replace("_", " ")
        lines.append(
            f"- {offer.carrier}{flight_number}: {offer.origin} → "
            f"{offer.destination}, departs {offer.depart_at.isoformat()}, "
            f"{_duration(offer.duration_minutes)} outbound, {_stops(offer.stops)}, "
            f"{journey} total {offer.total.amount:.0f} {offer.total.currency}"
        )
        lines.append(f"  {_return_line(offer)}")
    return "\n".join(lines)


def _duration(minutes: int | None) -> str:
    hours, remainder = divmod(max(0, minutes or 0), 60)
    return f"{hours}h{remainder:02d}m"


def _stops(stops: int | None) -> str:
    return "nonstop" if not stops else f"{stops} stop(s)"


def _return_line(offer: FlightOffer) -> str:
    if offer.journey_type != "round_trip":
        return "one-way offer (no return included)"
    if None in (
        offer.return_carrier,
        offer.return_origin,
        offer.return_destination,
        offer.return_depart_at,
    ):
        return "↩ return leg: unknown — do not invent a return flight"
    flight_number = f" {offer.return_flight_number}" if offer.return_flight_number else ""
    return (
        f"↩ return: {offer.return_carrier}{flight_number}: {offer.return_origin} → "
        f"{offer.return_destination}, departs {offer.return_depart_at.isoformat()}, "
        f"{_duration(offer.return_duration_minutes)}, {_stops(offer.return_stops)}"
    )
