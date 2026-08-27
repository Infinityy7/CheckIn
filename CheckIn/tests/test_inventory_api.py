"""Owned API wiring for hotel, flight and cart inventory."""

from __future__ import annotations

from fastapi.testclient import TestClient

import auth
import db
import main
from inventory.providers import DemoProvider, UnavailableProvider
from inventory.service import InventoryService, get_inventory_service
from schemas import AgentResult, Recommendation, TripPreferences
from store import create_trip


def _rec(category: str, index: int) -> Recommendation:
    return Recommendation(
        id=f"{category}-{index}", name=f"{category.title()} {index}", category=category,
        description="Controlled recommendation.", reasoning="Profile fit.", estimated_cost="$200",
        rating=4.5, location="Kyoto", image_search_query="Kyoto", rank=index, score=.8,
    )


def _seed_trip(tmp_path, user_id: str):
    state = create_trip(TripPreferences(
        destination="Kyoto", origin="Mumbai", start_date="2026-10-12", end_date="2026-10-18",
        budget_amount=3200, currency="USD", vibes=["culture"], group_type="couple", num_travelers=2,
    ), user_id=user_id)
    results = [
        AgentResult(agent_name=f"{category.title()} Agent", recommendations=[_rec(category, i) for i in range(1, 4)]).model_dump(mode="json")
        for category in ("hotel", "transport")
    ]
    db.mutate_trip_state(state.trip_id, lambda raw: raw.update({"research_results": results}))
    return state


def test_owned_inventory_routes_use_camel_case_and_reject_browser_prices(tmp_path):
    # Register against the same isolated database used by the trip.
    db.DB_PATH = tmp_path / "inventory-api.db"
    db.dispose_engine()
    db.init_db()
    token = auth.register("inventory@example.com", "safe-password-1")
    other_token = auth.register("other@example.com", "safe-password-2")
    user_id = db.get_user_by_email("inventory@example.com")["user_id"]
    state = _seed_trip(tmp_path, user_id)
    service = InventoryService(DemoProvider())
    main.app.dependency_overrides[get_inventory_service] = lambda: service
    client = TestClient(main.app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        rates_response = client.get(
            f"/api/trip/{state.trip_id}/hotels/hotel-1/rates", headers=headers
        )
        assert rates_response.status_code == 200
        rates = rates_response.json()
        assert rates["recommendationId"] == "hotel-1"
        assert rates["sourceMode"] == "demo"
        assert rates["isLive"] is False
        rate = rates["rooms"][0]["ratePlans"][0]
        assert "taxesAndFees" in rate

        forged = client.post(
            f"/api/trip/{state.trip_id}/cart/items",
            headers=headers,
            json={
                "recommendationId": "hotel-1", "ratePlanId": rate["id"], "kind": "hotel",
                "total": {"amount": 1, "currency": "USD"},
            },
        )
        assert forged.status_code == 422

        added = client.post(
            f"/api/trip/{state.trip_id}/cart/items",
            headers=headers,
            json={"recommendationId": "hotel-1", "ratePlanId": rate["id"], "kind": "hotel"},
        )
        assert added.status_code == 200
        assert added.json()["items"][0]["total"] == rate["total"]
        assert added.json()["items"][0]["status"] == "quoted"

        hidden = client.get(
            f"/api/trip/{state.trip_id}/cart",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert hidden.status_code == 404
        assert hidden.json()["error"]["code"] == "NOT_FOUND"
    finally:
        main.app.dependency_overrides.clear()


def test_unconfigured_provider_fails_closed_without_fake_inventory_or_secrets(tmp_path):
    db.DB_PATH = tmp_path / "inventory-disabled.db"
    db.dispose_engine()
    db.init_db()
    token = auth.register("disabled@example.com", "safe-password-1")
    user_id = db.get_user_by_email("disabled@example.com")["user_id"]
    state = _seed_trip(tmp_path, user_id)
    main.app.dependency_overrides[get_inventory_service] = lambda: InventoryService(UnavailableProvider())
    try:
        response = TestClient(main.app, raise_server_exceptions=False).get(
            f"/api/trip/{state.trip_id}/hotels/hotel-1/rates",
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": "inventory-disabled"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Live booking inventory is not connected on this deployment."
        assert response.json()["error"]["request_id"] == "inventory-disabled"
        assert "token" not in response.text.lower()
        assert "demo" not in response.text.lower()
    finally:
        main.app.dependency_overrides.clear()
