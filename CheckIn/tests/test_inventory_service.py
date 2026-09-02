"""Inventory/cart domain tests without supplier network traffic."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import db
from inventory.models import (
    AddCartItemInput,
    CartItemKind,
    CartItemState,
    Money,
)
from inventory.providers import DemoProvider
from inventory.service import CartVersionConflict, InventoryDomainError, InventoryService
from schemas import AgentResult, Recommendation, TripPreferences
from store import create_trip, get_trip, start_research


def _recommendation(category: str, index: int) -> Recommendation:
    return Recommendation(
        id=f"{category}-{index}",
        name=("Gion Garden House" if category == "hotel" else "Kyoto Complete Journey")
        + f" {index}",
        category=category,
        description="A researched option for controlled tests.",
        reasoning="It matches the saved profile.",
        estimated_cost="$120–$240",
        cost_min=120,
        cost_max=240,
        rating=4.7,
        review_count=1200,
        location="Kyoto",
        image_search_query="Kyoto travel",
        metadata={"booking_lookup_name": "Gion Garden House"} if category == "hotel" else {},
        rank=index,
        score=0.9,
    )


def _trip(tmp_path, *, user_id: str = "inventory-user"):
    db.DB_PATH = tmp_path / "inventory.db"
    db.dispose_engine()
    db.init_db()
    state = create_trip(
        TripPreferences(
            destination="Kyoto",
            origin="Mumbai",
            start_date="2026-10-12",
            end_date="2026-10-18",
            budget_amount=3200,
            currency="USD",
            vibes=["culture"],
            group_type="couple",
            num_travelers=2,
        ),
        user_id=user_id,
    )
    results = [
        AgentResult(
            agent_name=f"{category.title()} Agent",
            recommendations=[_recommendation(category, index) for index in range(1, 4)],
        ).model_dump(mode="json")
        for category in ("hotel", "transport", "restaurant")
    ]
    db.mutate_trip_state(state.trip_id, lambda raw: raw.update({"research_results": results}))
    return get_trip(state.trip_id)


def test_exact_hotel_rate_is_server_sourced_and_saved_timer_is_not_a_hold(tmp_path):
    now = [datetime(2026, 8, 1, 12, tzinfo=timezone.utc)]
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider(clock=lambda: now[0]), clock=lambda: now[0])

    inventory = asyncio.run(service.hotel_rates(state, "hotel-1"))
    rate = inventory.rooms[0].rate_plans[0]
    assert inventory.source_mode.value == "demo"
    assert inventory.is_live is False
    assert rate.total.amount > rate.nightly.amount
    assert rate.hold_expires_at is None
    assert db.load_trip_state(state.trip_id)["inventory_snapshots"]["hotels"]["hotel-1"]

    cart = asyncio.run(service.add_item(
        state,
        AddCartItemInput(
            recommendationId="hotel-1",
            ratePlanId=rate.id,
            kind="hotel",
        ),
    ))
    assert cart.items[0].total == rate.total
    assert cart.items[0].status == CartItemState.QUOTED
    assert cart.items[0].hold_expires_at is None
    assert cart.saved_expires_at == now[0] + timedelta(hours=1)
    assert "not reserved" in cart.reservation_notice.lower()

    now[0] += timedelta(hours=1, seconds=1)
    expired = service.cart(state)
    assert expired.items == []
    assert expired.saved_expires_at is None


def test_rate_must_belong_to_latest_owned_snapshot(tmp_path):
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider())

    with pytest.raises(InventoryDomainError, match="Check this hotel's current room prices"):
        asyncio.run(service.add_item(
            state,
            AddCartItemInput(recommendationId="hotel-1", ratePlanId="forged-rate", kind="hotel"),
        ))

    asyncio.run(service.hotel_rates(state, "hotel-1"))
    with pytest.raises(InventoryDomainError, match="not part of this trip"):
        asyncio.run(service.add_item(
            state,
            AddCartItemInput(recommendationId="hotel-1", ratePlanId="forged-rate", kind="hotel"),
        ))


def test_flight_offer_and_non_bookable_choices_share_cart_without_false_holds(tmp_path):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider(clock=lambda: now), clock=lambda: now)
    offers = asyncio.run(service.flight_offers(state, "transport-1"))

    cart = asyncio.run(service.add_item(
        state,
        AddCartItemInput(
            recommendationId="transport-1",
            ratePlanId=offers.offers[0].id,
            kind=CartItemKind.FLIGHT,
        ),
    ))
    assert cart.items[0].kind == CartItemKind.FLIGHT
    assert cart.items[0].status == CartItemState.QUOTED

    cart = asyncio.run(service.add_item(
        state,
        AddCartItemInput(recommendationId="restaurant-1", kind=CartItemKind.RESTAURANT),
    ))
    restaurant = next(item for item in cart.items if item.kind == CartItemKind.RESTAURANT)
    assert restaurant.status == CartItemState.SAVED
    assert restaurant.total is None
    assert restaurant.hold_expires_at is None

    cart = service.remove_item(state, restaurant.id)
    assert [item.kind for item in cart.items] == [CartItemKind.FLIGHT]


class _PriceChangeProvider(DemoProvider):
    async def revalidate_hotel_rate(self, rate_plan_id: str):
        quote = await super().revalidate_hotel_rate(rate_plan_id)
        return quote.model_copy(update={
            "total": Money(amount=quote.total.amount + 25, currency=quote.total.currency)
        })


def test_revalidation_surfaces_supplier_price_changes(tmp_path):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    state = _trip(tmp_path)
    service = InventoryService(_PriceChangeProvider(clock=lambda: now), clock=lambda: now)
    inventory = asyncio.run(service.hotel_rates(state, "hotel-1"))
    original = inventory.rooms[0].rate_plans[0].total.amount
    asyncio.run(service.add_item(
        state,
        AddCartItemInput(
            recommendationId="hotel-1",
            ratePlanId=inventory.rooms[0].rate_plans[0].id,
            kind="hotel",
        ),
    ))

    refreshed = asyncio.run(service.revalidate(state))
    assert refreshed.items[0].status == CartItemState.PRICE_CHANGED
    assert refreshed.items[0].total.amount == original + 25
    assert refreshed.state.value == "partial"


def test_full_research_refresh_invalidates_old_inventory_and_cart(tmp_path):
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider())
    inventory = asyncio.run(service.hotel_rates(state, "hotel-1"))
    asyncio.run(service.add_item(
        state,
        AddCartItemInput(
            recommendationId="hotel-1",
            ratePlanId=inventory.rooms[0].rate_plans[0].id,
            kind="hotel",
        ),
    ))

    start_research(state.trip_id, preserve_results=True, preserve_downstream=False)
    raw = db.load_trip_state(state.trip_id)
    assert raw["inventory_snapshots"] == {}
    assert raw["cart"] is None


def test_cart_mutations_are_versioned_and_interleaved_writes_keep_both_items(tmp_path):
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider())
    hotel = asyncio.run(service.hotel_rates(state, "hotel-1"))
    flights = asyncio.run(service.flight_offers(state, "transport-1"))
    rate = hotel.rooms[0].rate_plans[0]
    offer = flights.offers[0]

    stale_reader_a = service.cart(state)
    stale_reader_b = service.cart(state)
    assert stale_reader_a.version == 1
    assert stale_reader_b.version == 1

    first = asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="hotel-1", ratePlanId=rate.id, kind="hotel"),
    ))
    second = asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="transport-1", ratePlanId=offer.id, kind=CartItemKind.FLIGHT),
    ))
    assert first.version == 2
    assert second.version == 3
    assert {item.kind for item in second.items} == {CartItemKind.HOTEL, CartItemKind.FLIGHT}
    assert db.load_trip_state(state.trip_id)["cart"]["version"] == 3

    replaced = asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="hotel-1", ratePlanId=hotel.rooms[1].rate_plans[0].id, kind="hotel"),
    ))
    assert replaced.version == 4
    assert [item.rate_plan_id for item in replaced.items if item.kind == CartItemKind.HOTEL] == [hotel.rooms[1].rate_plans[0].id]

    with pytest.raises(CartVersionConflict):
        service.remove_item(state, replaced.items[0].id, expected_version=3)
    still = service.cart(state)
    assert still.version == 4
    assert len(still.items) == 2

    removed = service.remove_item(state, replaced.items[0].id, expected_version=4)
    assert removed.version == 5
    assert len(removed.items) == 1


def test_exact_choice_changes_clear_a_stored_itinerary_but_expiry_and_revalidation_do_not(tmp_path):
    now = [datetime(2026, 8, 1, 12, tzinfo=timezone.utc)]
    state = _trip(tmp_path)
    service = InventoryService(DemoProvider(clock=lambda: now[0]), clock=lambda: now[0])
    hotel = asyncio.run(service.hotel_rates(state, "hotel-1"))
    rate = hotel.rooms[0].rate_plans[0]
    plan = {"trip_title": "Kept", "trip_summary": "s", "days": []}

    def store_plan() -> None:
        db.mutate_trip_state(state.trip_id, lambda raw: raw.update({"itinerary": plan, "itinerary_fingerprint": "fp"}))

    store_plan()
    asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="restaurant-1", kind=CartItemKind.RESTAURANT),
    ))
    assert db.load_trip_state(state.trip_id)["itinerary"] == plan

    asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="hotel-1", ratePlanId=rate.id, kind="hotel"),
    ))
    assert db.load_trip_state(state.trip_id)["itinerary"] is None
    assert db.load_trip_state(state.trip_id)["itinerary_fingerprint"] is None

    store_plan()
    asyncio.run(service.revalidate(state))
    assert db.load_trip_state(state.trip_id)["itinerary"] == plan

    cart = service.cart(state)
    hotel_item = next(item for item in cart.items if item.kind == CartItemKind.HOTEL)
    service.remove_item(state, hotel_item.id)
    assert db.load_trip_state(state.trip_id)["itinerary"] is None

    asyncio.run(service.add_item(
        state, AddCartItemInput(recommendationId="hotel-1", ratePlanId=rate.id, kind="hotel"),
    ))
    store_plan()
    now[0] += timedelta(hours=1, seconds=1)
    expired = service.cart(state)
    assert expired.items == []
    assert expired.version > cart.version
    assert db.load_trip_state(state.trip_id)["itinerary"] == plan
