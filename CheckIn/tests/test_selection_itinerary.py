"""Selections, cart versions, and the itinerary lease/replay contract without model calls."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import auth
import db
import itinerary as itinerary_module
import main
from inventory.models import AddCartItemInput, CartItemKind
from inventory.providers import DemoProvider
from inventory.service import InventoryService, exact_cart_choices, get_inventory_service
from schemas import AgentResult, DayPlan, Itinerary, ItineraryItem, Recommendation, TripPreferences
from store import create_trip, selection_fingerprint, set_selections, start_itinerary

PREFERENCES = TripPreferences(
    destination="Kyoto",
    origin="Mumbai",
    start_date="2026-10-12",
    end_date="2026-10-18",
    budget_amount=3200,
    currency="USD",
    vibes=["culture"],
    group_type="couple",
    num_travelers=2,
)


def _rec(category: str, index: int) -> Recommendation:
    return Recommendation(
        id=f"{category}-{index}",
        name=f"{category.title()} option {index}",
        category=category,
        description="Controlled recommendation.",
        reasoning="Profile fit.",
        estimated_cost="$200",
        rating=4.5,
        location="Kyoto",
        image_search_query="Kyoto",
        metadata={"booking_lookup_name": "Gion Garden House"} if category == "hotel" else {},
        rank=index,
        score=0.8,
    )


def _plan(title: str = "Kyoto, gently") -> Itinerary:
    return Itinerary(
        trip_title=title,
        trip_summary="A calm route with room to wander.",
        days=[DayPlan(
            day_number=1,
            date="2026-10-12",
            theme="Arrival",
            items=[ItineraryItem(
                time_slot="3:00 PM - 4:00 PM",
                title="Check in",
                description="Settle in at Hotel option 1.",
                category="accommodation",
                cost_estimate="Included",
                location="Gion",
            )],
        )],
    )


def _seed(tmp_path, name: str):
    db.DB_PATH = tmp_path / f"{name}.db"
    db.dispose_engine()
    db.init_db()
    token = auth.register(f"{name}@example.com", "safe-password-1")
    user_id = db.get_user_by_email(f"{name}@example.com")["user_id"]
    state = create_trip(PREFERENCES, user_id=user_id)
    results = [
        AgentResult(
            agent_name=f"{category.title()} Agent",
            recommendations=[_rec(category, index) for index in range(1, 4)],
        ).model_dump(mode="json")
        for category in ("hotel", "transport", "restaurant")
    ]
    db.mutate_trip_state(
        state.trip_id,
        lambda raw: raw.update({"research_results": results, "context_brief": "A quiet Kyoto week."}),
    )
    return state.trip_id, {"Authorization": f"Bearer {token}"}


def test_selection_changes_invalidate_the_stored_itinerary_but_reorders_do_not(tmp_path):
    trip_id, headers = _seed(tmp_path, "selections")
    client = TestClient(main.app)
    select = f"/api/trip/{trip_id}/select"

    saved = client.post(select, headers=headers, json={"selections": ["hotel-1", "transport-1"]})
    assert saved.status_code == 200
    assert saved.json()["count"] == 2

    raw = db.load_trip_state(trip_id)
    fingerprint = selection_fingerprint(raw)
    db.mutate_trip_state(trip_id, lambda state: state.update({
        "itinerary": _plan().model_dump(mode="json"),
        "itinerary_fingerprint": fingerprint,
    }))

    reordered = client.post(select, headers=headers, json={"selections": ["transport-1", "hotel-1", "hotel-1"]})
    assert reordered.json()["count"] == 2
    unchanged = db.load_trip_state(trip_id)
    assert unchanged["selections"] == ["transport-1", "hotel-1"]
    assert unchanged["itinerary"]["trip_title"] == "Kyoto, gently"
    assert selection_fingerprint(unchanged) == fingerprint

    changed = client.post(select, headers=headers, json={"selections": ["hotel-1"]})
    assert changed.status_code == 200
    cleared = db.load_trip_state(trip_id)
    assert cleared["selections"] == ["hotel-1"]
    assert cleared["itinerary"] is None
    assert cleared["itinerary_fingerprint"] is None
    assert selection_fingerprint(cleared) != fingerprint

    reloaded = client.get(f"/api/trip/{trip_id}", headers=headers).json()
    assert reloaded["selections"] == ["hotel-1"]
    assert reloaded["itinerary"] is None


def test_fingerprint_covers_exact_cart_choices_not_cart_noise():
    base = {"selections": ["b", "a"], "cart": {"items": []}}
    same_order = {"selections": ["a", "b"], "cart": None}
    assert selection_fingerprint(base) == selection_fingerprint(same_order)

    with_hotel = {"selections": ["a", "b"], "cart": {"items": [
        {"kind": "hotel", "recommendation_id": "a", "rate_plan_id": "rate-1"},
    ]}}
    with_other_rate = {"selections": ["a", "b"], "cart": {"items": [
        {"kind": "hotel", "recommendation_id": "a", "rate_plan_id": "rate-2"},
    ]}}
    with_restaurant_only = {"selections": ["a", "b"], "cart": {"items": [
        {"kind": "restaurant", "recommendation_id": "b", "rate_plan_id": None},
    ]}}
    assert selection_fingerprint(with_hotel) != selection_fingerprint(base)
    assert selection_fingerprint(with_hotel) != selection_fingerprint(with_other_rate)
    assert selection_fingerprint(with_restaurant_only) == selection_fingerprint(base)


def test_itinerary_lease_replay_cart_invalidation_and_release(tmp_path, monkeypatch):
    trip_id, headers = _seed(tmp_path, "lease")
    generate_calls: list[list[dict] | None] = []

    async def fake_generate(_prefs, _brief, _selected, exact_choices=None):
        generate_calls.append(exact_choices)
        return _plan()

    monkeypatch.setattr(main, "generate_itinerary", fake_generate)
    service = InventoryService(DemoProvider())
    main.app.dependency_overrides[get_inventory_service] = lambda: service
    client = TestClient(main.app, raise_server_exceptions=False)
    itinerary_url = f"/api/trip/{trip_id}/itinerary"
    try:
        client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": ["hotel-1", "transport-1"]})

        start_itinerary(trip_id)
        blocked = client.post(itinerary_url, headers=headers)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "ITINERARY_IN_PROGRESS"
        assert blocked.json()["error"]["retryable"] is True
        assert generate_calls == []

        stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        db.mutate_trip_state(trip_id, lambda state: state.update({"itinerary_started_at": stale}))
        first = client.post(itinerary_url, headers=headers)
        assert first.status_code == 200
        assert '"event": "itinerary_complete"' in first.text
        assert '"replayed": true' not in first.text
        assert len(generate_calls) == 1
        assert generate_calls[0] == []
        raw = db.load_trip_state(trip_id)
        assert raw["itinerary_in_progress"] is False
        assert raw["itinerary_lease_id"] is None
        assert raw["itinerary_fingerprint"] == selection_fingerprint(raw)

        replayed = client.post(itinerary_url, headers=headers)
        assert replayed.status_code == 200
        assert '"event": "itinerary_complete"' in replayed.text
        assert '"replayed": true' in replayed.text
        assert '"trip_title": "Kyoto, gently"' in replayed.text
        assert len(generate_calls) == 1

        rates = client.get(f"/api/trip/{trip_id}/hotels/hotel-1/rates", headers=headers).json()
        room = rates["rooms"][1]
        rate = room["ratePlans"][1]
        added = client.post(
            f"/api/trip/{trip_id}/cart/items",
            headers=headers,
            json={"recommendationId": "hotel-1", "ratePlanId": rate["id"], "kind": "hotel"},
        )
        assert added.status_code == 200
        assert added.json()["version"] == 2
        assert db.load_trip_state(trip_id)["itinerary"] is None

        rebuilt = client.post(itinerary_url, headers=headers)
        assert rebuilt.status_code == 200
        assert len(generate_calls) == 2
        exact = generate_calls[1]
        assert exact is not None and len(exact) == 1
        assert exact[0]["kind"] == "hotel"
        assert exact[0]["name"] == "Gion Garden House"
        assert exact[0]["room"] == room["name"]
        assert exact[0]["rate"] == rate["label"]
        assert exact[0]["check_in"] == "2026-10-12"
        assert exact[0]["check_out"] == "2026-10-18"
        assert exact[0]["total"].endswith("USD")

        async def failing_generate(*_args, **_kwargs):
            raise RuntimeError("model unavailable")

        monkeypatch.setattr(main, "generate_itinerary", failing_generate)
        client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": ["hotel-1"]})
        failed = client.post(itinerary_url, headers=headers)
        assert '"code": "ITINERARY_FAILED"' in failed.text
        after_failure = db.load_trip_state(trip_id)
        assert after_failure["itinerary_in_progress"] is False
        assert after_failure["itinerary"] is None
    finally:
        main.app.dependency_overrides.clear()


def test_itinerary_still_requires_selections_and_results(tmp_path):
    trip_id, headers = _seed(tmp_path, "guards")
    client = TestClient(main.app, raise_server_exceptions=False)

    no_selection = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert no_selection.status_code == 400

    set_selections(trip_id, ["hotel-1"])
    db.mutate_trip_state(trip_id, lambda state: state.update({"research_results": []}))
    no_results = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert no_results.status_code == 400


def test_exact_cart_choices_resolve_rooms_and_flights_from_snapshots(tmp_path):
    trip_id, _headers = _seed(tmp_path, "choices")
    state = main.get_trip(trip_id)
    service = InventoryService(DemoProvider())
    hotel = asyncio.run(service.hotel_rates(state, "hotel-1"))
    flights = asyncio.run(service.flight_offers(state, "transport-1"))
    rate = hotel.rooms[2].rate_plans[0]
    offer = flights.offers[0]
    asyncio.run(service.add_item(state, AddCartItemInput(recommendationId="hotel-1", ratePlanId=rate.id, kind="hotel")))
    asyncio.run(service.add_item(state, AddCartItemInput(recommendationId="transport-1", ratePlanId=offer.id, kind=CartItemKind.FLIGHT)))
    asyncio.run(service.add_item(state, AddCartItemInput(recommendationId="restaurant-1", kind=CartItemKind.RESTAURANT)))

    choices = exact_cart_choices(db.load_trip_state(trip_id))
    assert [choice["kind"] for choice in choices] == ["hotel", "flight"]
    room_choice, flight_choice = choices
    assert room_choice["room"] == hotel.rooms[2].name
    assert room_choice["rate"] == rate.label
    assert room_choice["refundable"] == rate.refundable
    assert room_choice["total"] == f"{rate.total.amount:.2f} USD"
    assert flight_choice["carrier"] == offer.carrier
    assert flight_choice["flight_number"] == offer.flight_number
    assert flight_choice["route"] == f"{offer.origin} -> {offer.destination}"
    assert flight_choice["depart_at"] == offer.depart_at.isoformat()
    assert flight_choice["journey_type"] == "round_trip"
    assert "return_leg" in flight_choice

    prompt_json = json.dumps(choices)
    assert "USD" in prompt_json


def test_exact_choices_are_rendered_into_the_model_prompt(monkeypatch):
    prompts: list[str] = []
    selected = [_rec("hotel", 1)]
    plan = Itinerary(
        trip_title="One night",
        trip_summary="Short and sweet.",
        days=[DayPlan(day_number=1, date="2026-10-12", theme="Arrive", items=[ItineraryItem(
            time_slot="3:00 PM", title="Check in at Hotel option 1", description="View Room, Flexible + breakfast.",
            category="accommodation", cost_estimate="Included", location="Gion",
        )])],
    )
    prefs = PREFERENCES.model_copy(update={"end_date": PREFERENCES.start_date})

    async def fake_generate_text(prompt: str, **_kwargs):
        prompts.append(prompt)
        return json.dumps(plan.model_dump())

    monkeypatch.setattr(itinerary_module, "generate_text", fake_generate_text)
    result = asyncio.run(itinerary_module.generate_itinerary(prefs, "brief", selected, exact_choices=[
        {"kind": "hotel", "name": "Gion Garden House", "room": "View Room", "rate": "Flexible + breakfast", "total": "1450.00 USD"},
        {"kind": "flight", "carrier": "Atlas Demo Air", "flight_number": "DM210", "route": "BOM -> KIX"},
    ]))
    assert result.trip_title == "One night"
    assert len(prompts) == 1
    assert "## Exact booked/quoted choices" in prompts[0]
    assert "View Room" in prompts[0]
    assert "Flexible + breakfast" in prompts[0]
    assert "DM210" in prompts[0]
    assert "Never substitute" in prompts[0]

    prompts.clear()
    asyncio.run(itinerary_module.generate_itinerary(prefs, "brief", selected))
    assert "## Exact booked/quoted choices" not in prompts[0]


def test_client_disconnect_releases_the_itinerary_lease(tmp_path, monkeypatch):
    trip_id, headers = _seed(tmp_path, "disconnect")
    set_selections(trip_id, ["hotel-1"])

    async def never_finishes(*_args, **_kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "generate_itinerary", never_finishes)

    async def drive():
        first_chunk = asyncio.Event()

        async def receive():
            await first_chunk.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                first_chunk.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": f"/api/trip/{trip_id}/itinerary",
            "raw_path": f"/api/trip/{trip_id}/itinerary".encode(), "query_string": b"", "root_path": "",
            "headers": [(b"authorization", headers["Authorization"].encode()), (b"host", b"testserver")],
            "client": ("127.0.0.1", 4321), "server": ("testserver", 80),
        }
        try:
            await asyncio.wait_for(main.app(scope, receive, send), timeout=10)
        except Exception:
            pass

    asyncio.run(drive())

    raw = db.load_trip_state(trip_id)
    assert raw["itinerary_in_progress"] is False
    assert raw.get("itinerary_lease_id") is None
    assert start_itinerary(trip_id)


def test_selection_change_during_the_build_discards_the_stale_itinerary(tmp_path, monkeypatch):
    trip_id, headers = _seed(tmp_path, "midbuild")
    client = TestClient(main.app, raise_server_exceptions=False)
    client.post(f"/api/trip/{trip_id}/select", headers=headers, json={"selections": ["hotel-1"]})

    async def changes_selections_midway(*_args, **_kwargs):
        set_selections(trip_id, ["hotel-1", "restaurant-1"])
        return _plan()

    monkeypatch.setattr(main, "generate_itinerary", changes_selections_midway)
    stale = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert stale.status_code == 200
    assert '"code": "ITINERARY_INPUTS_CHANGED"' in stale.text
    assert '"event": "itinerary_complete"' not in stale.text
    raw = db.load_trip_state(trip_id)
    assert raw.get("itinerary") is None
    assert raw["itinerary_in_progress"] is False

    async def fine(*_args, **_kwargs):
        return _plan()

    monkeypatch.setattr(main, "generate_itinerary", fine)
    fresh = client.post(f"/api/trip/{trip_id}/itinerary", headers=headers)
    assert '"event": "itinerary_complete"' in fresh.text
    assert db.load_trip_state(trip_id)["itinerary_fingerprint"] == selection_fingerprint(db.load_trip_state(trip_id))
