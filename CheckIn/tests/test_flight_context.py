"""Supplier flight briefing: fail-soft fetch and honest prompt wiring."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import agents.flight_context as flight_context
from agents.transport import TransportAgent
from inventory.models import (
    AvailabilityStatus,
    FlightInventory,
    FlightOffer,
    Money,
    SourceMode,
)
from schemas import GroupType, TripPreferences


def _prefs() -> TripPreferences:
    return TripPreferences(
        destination="Tokyo",
        origin="Mumbai",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
        budget_amount=4200,
        currency="USD",
        vibes=["culture", "food"],
        group_type=GroupType.COUPLE,
        num_travelers=2,
    )


def _inventory(source_mode: SourceMode = SourceMode.LIVE) -> FlightInventory:
    is_live = source_mode == SourceMode.LIVE
    depart = datetime(2026, 10, 12, 2, 30, tzinfo=timezone.utc)
    offers = [
        FlightOffer(
            id="off_1",
            carrier="All Nippon Airways",
            flight_number="NH830",
            origin="BOM",
            destination="NRT",
            depart_at=depart,
            arrive_at=datetime(2026, 10, 12, 12, 5, tzinfo=timezone.utc),
            duration_minutes=575,
            stops=0,
            journey_type="round_trip",
            return_carrier="All Nippon Airways",
            return_flight_number="NH829",
            return_origin="NRT",
            return_destination="BOM",
            return_depart_at=datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc),
            return_arrive_at=datetime(2026, 10, 18, 20, 50, tzinfo=timezone.utc),
            return_duration_minutes=590,
            return_stops=0,
            total=Money(amount=1840, currency="USD"),
            availability_status=AvailabilityStatus.AVAILABLE,
            source="duffel",
            source_mode=source_mode,
            is_live=is_live,
        ),
        FlightOffer(
            id="off_2",
            carrier="Singapore Airlines",
            origin="BOM",
            destination="HND",
            depart_at=depart,
            arrive_at=datetime(2026, 10, 12, 16, 40, tzinfo=timezone.utc),
            duration_minutes=850,
            stops=1,
            journey_type="round_trip",
            total=Money(amount=1420, currency="USD"),
            availability_status=AvailabilityStatus.AVAILABLE,
            source="duffel",
            source_mode=source_mode,
            is_live=is_live,
        ),
    ]
    return FlightInventory(
        recommendation_id="transport-briefing",
        source="duffel",
        source_mode=source_mode,
        is_live=is_live,
        checked_at=depart,
        offers=offers,
    )


class _StubProvider:
    def __init__(self, source_mode: SourceMode, result: FlightInventory | Exception):
        self.source_mode = source_mode
        self.is_live = source_mode == SourceMode.LIVE
        self.result = result
        self.calls: list[dict] = []

    async def search_flight_inventory(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _StubService:
    def __init__(self, provider):
        self.provider = provider


def _patch_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        flight_context, "get_inventory_service", lambda: _StubService(provider)
    )


def test_briefing_formats_offers_for_the_whole_party():
    briefing = flight_context.format_flight_briefing(_inventory(), adults=2)

    assert "live bookable" in briefing
    assert "party of 2" in briefing
    assert "All Nippon Airways NH830: BOM → NRT" in briefing
    assert "9h35m outbound, nonstop" in briefing
    assert "1840 USD" in briefing
    assert "1 stop(s)" in briefing
    assert "searching the web" in briefing


def test_briefing_prints_supplier_return_leg():
    briefing = flight_context.format_flight_briefing(_inventory(), adults=2)

    assert (
        "↩ return: All Nippon Airways NH829: NRT → BOM, "
        "departs 2026-10-18T11:00:00+00:00, 9h50m, nonstop"
    ) in briefing
    assert "lists its outbound leg and then its return leg" in briefing


def test_briefing_marks_unknown_return_leg_instead_of_inventing_one():
    briefing = flight_context.format_flight_briefing(_inventory(), adults=2)
    lines = briefing.splitlines()

    singapore = next(index for index, line in enumerate(lines) if "Singapore Airlines" in line)
    assert lines[singapore + 1].strip() == "↩ return leg: unknown — do not invent a return flight"
    assert briefing.count("return leg: unknown") == 1


def test_briefing_labels_one_way_offers():
    one_way = _inventory().model_copy(update={
        "offers": [
            offer.model_copy(update={
                "journey_type": "one_way",
                "return_carrier": None,
                "return_flight_number": None,
                "return_origin": None,
                "return_destination": None,
                "return_depart_at": None,
                "return_arrive_at": None,
                "return_duration_minutes": None,
                "return_stops": None,
            })
            for offer in _inventory().offers
        ],
    })
    briefing = flight_context.format_flight_briefing(one_way, adults=2)

    assert briefing.count("one-way offer (no return included)") == 2
    assert "↩ return" not in briefing
    assert "one way total 1840 USD" in briefing


def test_briefing_labels_test_mode_honestly():
    briefing = flight_context.format_flight_briefing(
        _inventory(SourceMode.TEST), adults=2
    )
    assert "supplier test-mode" in briefing
    assert "live bookable" not in briefing


def test_briefing_fetch_passes_trip_facts(monkeypatch):
    provider = _StubProvider(SourceMode.TEST, _inventory(SourceMode.TEST))
    _patch_provider(monkeypatch, provider)

    briefing = asyncio.run(flight_context.build_flight_briefing(_prefs()))

    assert briefing is not None
    call = provider.calls[0]
    assert call["origin"] == "Mumbai"
    assert call["destination"] == "Tokyo"
    assert call["departure_date"] == date(2026, 10, 12)
    assert call["return_date"] == date(2026, 10, 18)
    assert call["adults"] == 2


def test_briefing_skips_demo_and_unconfigured_inventory(monkeypatch):
    for mode in (SourceMode.DEMO, SourceMode.UNAVAILABLE):
        provider = _StubProvider(mode, _inventory(SourceMode.TEST))
        _patch_provider(monkeypatch, provider)
        assert asyncio.run(flight_context.build_flight_briefing(_prefs())) is None
        assert provider.calls == []


def test_briefing_fails_soft_on_provider_error(monkeypatch):
    provider = _StubProvider(SourceMode.LIVE, RuntimeError("supplier down"))
    _patch_provider(monkeypatch, provider)
    assert asyncio.run(flight_context.build_flight_briefing(_prefs())) is None


def test_briefing_returns_none_when_no_offers(monkeypatch):
    empty = _inventory(SourceMode.TEST).model_copy(update={"offers": []})
    provider = _StubProvider(SourceMode.TEST, empty)
    _patch_provider(monkeypatch, provider)
    assert asyncio.run(flight_context.build_flight_briefing(_prefs())) is None


def test_transport_prompt_pivots_on_supplier_offers(monkeypatch):
    agent = TransportAgent()

    async def briefing(_prefs):
        return "## Supplier flight offers (CheckIn inventory API)\n- offer"

    monkeypatch.setattr(
        "agents.transport.build_flight_briefing", briefing
    )
    enriched = asyncio.run(agent.prepare_context(_prefs(), "the brief"))
    assert enriched.startswith("the brief")
    assert "Supplier flight offers" in enriched

    prompt = agent.build_user_prompt(_prefs(), enriched)
    assert "Do NOT search the web for flights" in prompt
    assert "No supplier flight data" not in prompt


def test_transport_prompt_forbids_inventing_an_unknown_return_leg(monkeypatch):
    agent = TransportAgent()

    async def briefing(_prefs):
        return flight_context.format_flight_briefing(_inventory(), adults=2)

    monkeypatch.setattr("agents.transport.build_flight_briefing", briefing)
    enriched = asyncio.run(agent.prepare_context(_prefs(), "the brief"))
    prompt = agent.build_user_prompt(_prefs(), enriched)

    assert "return leg: unknown — do not invent a return flight" in prompt
    assert 'set return.flight.carrier_hint to exactly "unknown — check live"' in prompt
    assert "do NOT invent a carrier, departure time, or price for that return leg" in prompt
    assert '"return": {' in prompt
    assert '"outbound": {' in prompt


def test_transport_prompt_falls_back_to_labelled_estimates(monkeypatch):
    agent = TransportAgent()

    async def briefing(_prefs):
        return None

    monkeypatch.setattr(
        "agents.transport.build_flight_briefing", briefing
    )
    enriched = asyncio.run(agent.prepare_context(_prefs(), "the brief"))
    assert enriched == "the brief"

    prompt = agent.build_user_prompt(_prefs(), enriched)
    assert "No supplier flight data is available" in prompt
    assert "Do NOT spend web searches researching fares" in prompt
