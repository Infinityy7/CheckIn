"""Small API-contract guards used by the quality pipeline.

These tests do not call an LLM, supplier, payment processor, or production DB.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import main


def test_openapi_operations_are_unique_and_document_success_responses():
    document = main.app.openapi()
    operation_ids: list[str] = []

    assert document["info"]["title"] == "CheckIn"
    assert document["paths"]["/api/health"]["get"]
    for path, path_item in document["paths"].items():
        assert path.startswith("/api/"), f"Unexpected public route in API contract: {path}"
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            assert operation_id, f"{method.upper()} {path} has no operationId"
            operation_ids.append(operation_id)
            assert "200" in operation["responses"], f"{method.upper()} {path} does not document success"

    assert len(operation_ids) == len(set(operation_ids)), "OpenAPI operationId values must be unique"


def test_high_risk_mutations_reject_missing_auth_before_processing_body():
    client = TestClient(main.app)
    protected_mutations = [
        ("POST", "/api/profile/intake/complete"),
        ("PUT", "/api/profile/character"),
        ("POST", "/api/profile/character/reset"),
        ("POST", "/api/trip/preferences"),
        ("POST", "/api/trip/not-owned/research"),
        ("POST", "/api/trip/not-owned/select"),
        ("POST", "/api/trip/not-owned/itinerary"),
        ("GET", "/api/trip/not-owned/hotels/hotel-1/rates"),
        ("GET", "/api/trip/not-owned/flights/transport-1/offers"),
        ("GET", "/api/trip/not-owned/cart"),
        ("POST", "/api/trip/not-owned/cart/items"),
        ("DELETE", "/api/trip/not-owned/cart/items/cart-item-1"),
        ("POST", "/api/trip/not-owned/cart/revalidate"),
    ]

    for method, path in protected_mutations:
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} was not protected"
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
        assert response.headers["X-Request-ID"]
