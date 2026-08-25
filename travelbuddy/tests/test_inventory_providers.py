"""Contract tests for supplier inventory adapters.

The Duffel tests use ``httpx.MockTransport`` so they exercise the real request
and normalization boundary without making network calls or requiring secrets.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx
import pytest

from inventory.models import AvailabilityStatus, SourceMode
from inventory.providers import (
    DemoProvider,
    DuffelProvider,
    InventoryProviderError,
    ProviderConfigurationError,
    ProviderDataError,
    ProviderItemUnavailableError,
    ProviderUnavailableError,
    UnavailableProvider,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def run(coro):
    """Run one adapter coroutine without requiring a pytest async plugin."""

    return asyncio.run(coro)


def request_json(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


def mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def room_payload() -> dict[str, Any]:
    return {
        "id": "room-deluxe",
        "name": "Deluxe King",
        "description": "A high-floor room overlooking the old town.",
        "beds": [{"type": "king", "count": 1}],
        "photos": [{"url": "https://images.example/room.jpg"}],
        "rates": [
            {
                "id": "rate-flex",
                "name": "Breakfast + flexible",
                "description": "Breakfast for two.",
                "total_amount": "448.00",
                "total_currency": "usd",
                "base_amount": "400.00",
                "base_currency": "USD",
                "tax_amount": "32.00",
                "tax_currency": "USD",
                "fee_amount": "16.00",
                "fee_currency": "USD",
                "due_at_accommodation_amount": "20.00",
                "due_at_accommodation_currency": "USD",
                "quantity_available": 2,
                "board_type": "breakfast_included",
                "payment_type": "pay_now",
                "expires_at": "2026-08-01T13:00:00Z",
                # Deliberately reversed: the adapter must present a timeline.
                "cancellation_timeline": [
                    {
                        "before": "2026-09-10T18:00:00Z",
                        "refund_amount": "224.00",
                        "currency": "USD",
                    },
                    {
                        "before": "2026-09-01T18:00:00Z",
                        "refund_amount": "448.00",
                        "currency": "USD",
                    },
                ],
            },
            {
                "id": "rate-fixed",
                "name": "Room only",
                "total_amount": "400.00",
                "total_currency": "USD",
                "tax_amount": "0",
                "tax_currency": "USD",
                "quantity_available": 0,
                "cancellation_timeline": [],
            },
        ],
    }


def test_duffel_hotel_request_contract_and_normalization():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/stays/accommodation/suggestions":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "accommodation_id": "acc-other",
                            "accommodation_name": "Other Hotel",
                        },
                        {
                            "accommodation_id": "acc-kyoto",
                            "accommodation_name": "Kyoto Lantern House",
                        },
                    ]
                },
            )
        if request.url.path == "/stays/search":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "results": [
                            {
                                "id": "search-result-1",
                                "expires_at": "2026-08-01T13:00:00Z",
                                "accommodation": {
                                    "id": "acc-kyoto",
                                    "name": "Kyoto Lantern House",
                                    # Force the documented fetch-all-rates step.
                                    "rooms": [],
                                },
                            }
                        ]
                    }
                },
            )
        assert request.url.path == (
            "/stays/search_results/search-result-1/actions/fetch_all_rates"
        )
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "search-result-1",
                    "expires_at": "2026-08-01T13:00:00Z",
                    "accommodation": {
                        "id": "acc-kyoto",
                        "name": "Kyoto Lantern House",
                        "rooms": [room_payload()],
                    },
                }
            },
        )

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider(
                "duffel_test_token",
                http_client=client,
                source_mode=SourceMode.TEST,
                clock=lambda: NOW,
            )
            return await provider.search_hotel_inventory(
                recommendation_id="hotel-rec-1",
                hotel_name="  Kyoto Lantern House ",
                check_in_date=date(2026, 9, 12),
                check_out_date=date(2026, 9, 14),
                adults=2,
                children=1,
                rooms=1,
            )

    inventory = run(scenario())

    assert [request.url.path for request in requests] == [
        "/stays/accommodation/suggestions",
        "/stays/search",
        "/stays/search_results/search-result-1/actions/fetch_all_rates",
    ]
    for request in requests:
        assert request.headers["authorization"] == "Bearer duffel_test_token"
        assert request.headers["duffel-version"] == "v2"
        assert request.headers["accept"] == "application/json"
        assert request.headers["content-type"] == "application/json"

    assert request_json(requests[0]) == {
        "data": {"query": "  Kyoto Lantern House "}
    }
    assert request_json(requests[1]) == {
        "data": {
            "accommodation": {"ids": ["acc-kyoto"], "fetch_rates": True},
            "check_in_date": "2026-09-12",
            "check_out_date": "2026-09-14",
            "guests": [
                {"type": "adult"},
                {"type": "adult"},
                {"type": "child", "age": 8},
            ],
            "rooms": 1,
        }
    }
    assert requests[2].content == b""

    assert inventory.hotel_id == "acc-kyoto"
    assert inventory.recommendation_id == "hotel-rec-1"
    assert inventory.hotel_name == "Kyoto Lantern House"
    assert inventory.source_mode == SourceMode.TEST
    assert inventory.is_live is False
    assert inventory.checked_at == NOW
    assert inventory.source_metadata == {"search_result_id": "search-result-1"}

    room = inventory.rooms[0]
    assert room.name == "Deluxe King"
    assert room.beds[0].type == "king"
    assert room.photos == ["https://images.example/room.jpg"]
    flexible, fixed = room.rate_plans
    assert flexible.total.amount == 448
    assert flexible.total.currency == "USD"
    assert flexible.nightly.amount == 224
    assert flexible.taxes_and_fees.amount == 48
    assert flexible.due_at_property.amount == 20
    assert flexible.rooms_remaining == 2
    assert flexible.availability_status == AvailabilityStatus.LIMITED
    assert flexible.refundable is True
    assert flexible.cancellation_summary.startswith("Full refund before")
    assert [window.refund.amount for window in flexible.cancellation.timeline] == [
        448,
        224,
    ]
    assert flexible.provider_metadata == {
        "search_result_id": "search-result-1",
        "rate_id": "rate-flex",
    }
    assert fixed.availability_status == AvailabilityStatus.UNAVAILABLE
    assert fixed.refundable is False
    assert fixed.cancellation_summary == "Non-refundable."


def test_duffel_flight_place_lookup_request_shape_and_normalization():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/places/suggestions":
            query = request.url.params["query"]
            return httpx.Response(
                200,
                json={"data": [{"iata_code": "BOM" if query == "Mumbai" else "NRT"}]},
            )
        assert request.url.path == "/air/offer_requests"
        return httpx.Response(
            200,
            json={
                "data": {
                    "live_mode": False,
                    "offers": [
                        {
                            "id": "off-1",
                            "offer_request_id": "orq-1",
                            "total_amount": "902.40",
                            "total_currency": "usd",
                            "expires_at": "2099-08-01T12:30:00Z",
                            "payment_requirements": {
                                "requires_instant_payment": True
                            },
                            "slices": [
                                {
                                    "segments": [
                                        {
                                            "departing_at": "2026-09-12T08:00:00Z",
                                            "arriving_at": "2026-09-12T10:00:00Z",
                                            "origin": {"iata_code": "BOM"},
                                            "destination": {"iata_code": "DXB"},
                                            "operating_carrier": {
                                                "name": "Test Air",
                                                "iata_code": "TA",
                                            },
                                            "marketing_carrier": {
                                                "name": "Test Air",
                                                "iata_code": "TA",
                                            },
                                            "marketing_carrier_flight_number": "101",
                                        },
                                        {
                                            "departing_at": "2026-09-12T11:15:00Z",
                                            "arriving_at": "2026-09-12T18:45:00Z",
                                            "origin": {"iata_code": "DXB"},
                                            "destination": {"iata_code": "NRT"},
                                            "operating_carrier": {
                                                "name": "Test Air",
                                                "iata_code": "TA",
                                            },
                                        },
                                    ]
                                },
                                {
                                    "segments": [
                                        {
                                            "departing_at": "2026-09-20T09:30:00Z",
                                            "arriving_at": "2026-09-20T17:30:00Z",
                                            "origin": {"iata_code": "NRT"},
                                            "destination": {"iata_code": "BOM"},
                                            "operating_carrier": {
                                                "name": "Test Air",
                                                "iata_code": "TA",
                                            },
                                        }
                                    ]
                                },
                            ],
                        }
                    ],
                }
            },
        )

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider(
                "token", http_client=client, clock=lambda: NOW
            )
            return await provider.search_flight_inventory(
                recommendation_id="transport-rec-1",
                origin="Mumbai",
                destination="Tokyo",
                departure_date=date(2026, 9, 12),
                return_date=date(2026, 9, 20),
                adults=2,
                cabin_class="premium_economy",
                limit=3,
            )

    inventory = run(scenario())
    place_requests = [
        request for request in requests if request.url.path == "/places/suggestions"
    ]
    assert {request.url.params["query"] for request in place_requests} == {
        "Mumbai",
        "Tokyo",
    }
    offer_request = next(
        request for request in requests if request.url.path == "/air/offer_requests"
    )
    assert offer_request.url.params["return_offers"] == "true"
    assert offer_request.url.params["supplier_timeout"] == "15000"
    assert request_json(offer_request) == {
        "data": {
            "slices": [
                {
                    "origin": "BOM",
                    "destination": "NRT",
                    "departure_date": "2026-09-12",
                },
                {
                    "origin": "NRT",
                    "destination": "BOM",
                    "departure_date": "2026-09-20",
                },
            ],
            "passengers": [{"type": "adult"}, {"type": "adult"}],
            "cabin_class": "premium_economy",
        }
    }

    assert inventory.recommendation_id == "transport-rec-1"
    assert inventory.source_mode == SourceMode.TEST
    assert inventory.is_live is False
    offer = inventory.offers[0]
    assert offer.id == "off-1"
    assert offer.carrier == "Test Air"
    assert offer.flight_number == "TA101"
    assert offer.origin == "BOM"
    # The card summarizes the outbound slice; the full offer still represents
    # a return journey and is labelled as such.
    assert offer.destination == "NRT"
    assert offer.duration_minutes == 645
    assert offer.stops == 1
    assert offer.journey_type == "round_trip"
    assert offer.total.amount == 902.40
    assert offer.availability_status == AvailabilityStatus.AVAILABLE


def test_duffel_iata_places_bypass_lookup_and_limit_is_applied():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/air/offer_requests"
        offer = {
            "id": "offer-1",
            "total_amount": "100.00",
            "total_currency": "USD",
            "expires_at": "2099-08-01T12:30:00Z",
            "slices": [
                {
                    "segments": [
                        {
                            "departing_at": "2026-09-12T08:00:00Z",
                            "arriving_at": "2026-09-12T09:00:00Z",
                            "origin": {"iata_code": "JFK"},
                            "destination": {"iata_code": "LHR"},
                            "operating_carrier": {"name": "Example Air"},
                        }
                    ]
                }
            ],
        }
        return httpx.Response(
            200,
            json={"data": {"live_mode": True, "offers": [offer, offer]}},
        )

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider("token", http_client=client)
            return await provider.search_flight_inventory(
                recommendation_id="rec",
                origin="jfk",
                destination="lhr",
                departure_date=date(2026, 9, 12),
                return_date=None,
                adults=1,
                limit=1,
            )

    inventory = run(scenario())
    assert [request.url.path for request in requests] == ["/air/offer_requests"]
    assert len(inventory.offers) == 1
    assert inventory.source_mode == SourceMode.LIVE
    assert inventory.is_live is True
    assert inventory.offers[0].is_live is True


def test_duffel_revalidates_hotel_rate_and_flight_offer():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/stays/quotes":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "quote-1",
                        "total_amount": "512.25",
                        "total_currency": "EUR",
                        "expires_at": "2026-08-01T12:20:00Z",
                    }
                },
            )
        assert request.url.path == "/air/offers/offer/with spaces"
        assert request.url.raw_path == b"/air/offers/offer%2Fwith%20spaces"
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": "offer/with spaces",
                    "total_amount": "799.00",
                    "total_currency": "USD",
                    "expires_at": "2026-08-01T11:59:00Z",
                    "live_mode": False,
                }
            },
        )

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider(
                "token", http_client=client, clock=lambda: NOW
            )
            hotel = await provider.revalidate_hotel_rate("rate-1")
            flight = await provider.revalidate_flight_offer("offer/with spaces")
            return hotel, flight

    hotel, flight = run(scenario())
    assert request_json(requests[0]) == {"data": {"rate_id": "rate-1"}}
    assert hotel.provider_reference == "quote-1"
    assert hotel.total.amount == 512.25
    assert hotel.available is True
    assert hotel.quote_expires_at.isoformat() == "2026-08-01T12:20:00+00:00"
    assert flight.provider_reference == "offer/with spaces"
    assert flight.total.amount == 799
    assert flight.available is False
    assert flight.source_mode == SourceMode.TEST
    assert flight.is_live is False


def test_duffel_rejects_missing_credentials():
    with pytest.raises(ProviderConfigurationError) as caught:
        DuffelProvider("   ")

    assert caught.value.code == "INVENTORY_NOT_CONFIGURED"
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    ("status", "payload", "error_type", "retryable"),
    [
        (401, {"errors": [{"code": "unauthorized"}]}, ProviderConfigurationError, False),
        (404, {"errors": [{"code": "rate_unavailable"}]}, ProviderItemUnavailableError, False),
        (429, {"errors": [{"code": "rate_limit_exceeded"}]}, ProviderUnavailableError, True),
        (503, {"errors": [{"code": "server_error"}]}, ProviderUnavailableError, True),
        (422, {"errors": [{"code": "invalid_request"}]}, InventoryProviderError, False),
    ],
)
def test_duffel_classifies_supplier_http_failures(
    status: int,
    payload: dict[str, Any],
    error_type: type[InventoryProviderError],
    retryable: bool,
):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider("token", http_client=client)
            return await provider.suggest_place("Tokyo")

    with pytest.raises(error_type) as caught:
        run(scenario())
    assert caught.value.retryable is retryable
    assert "token" not in str(caught.value).lower()


def test_duffel_classifies_network_failure_as_retryable_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("supplier was slow", request=request)

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider("secret-token", http_client=client)
            return await provider.suggest_place("Tokyo")

    with pytest.raises(ProviderUnavailableError) as caught:
        run(scenario())
    assert caught.value.code == "INVENTORY_PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"data": [{"name": "No IATA here"}]}),
    ],
)
def test_duffel_fails_closed_on_malformed_supplier_data(response: httpx.Response):
    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider("token", http_client=client)
            return await provider.suggest_place("Tokyo")

    with pytest.raises(ProviderDataError):
        run(scenario())


def test_duffel_fails_closed_on_malformed_room_cancellation_currency():
    bad_room = room_payload()
    bad_room["rates"][0]["cancellation_timeline"][0]["currency"] = "EUR"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("suggestions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "accommodation_id": "acc-1",
                            "accommodation_name": "Hotel",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "results": [
                        {
                            "id": "result-1",
                            "accommodation": {
                                "id": "acc-1",
                                "name": "Hotel",
                                "rooms": [bad_room],
                            },
                        }
                    ]
                }
            },
        )

    async def scenario():
        async with mock_client(handler) as client:
            provider = DuffelProvider("token", http_client=client)
            return await provider.search_hotel_inventory(
                recommendation_id="rec",
                hotel_name="Hotel",
                check_in_date=date(2026, 9, 12),
                check_out_date=date(2026, 9, 14),
                adults=1,
            )

    with pytest.raises(ProviderDataError, match="mixed cancellation currencies"):
        run(scenario())


def test_demo_provider_is_explicitly_non_live_across_inventory_and_quotes():
    provider = DemoProvider(clock=lambda: NOW)

    async def scenario():
        hotel = await provider.search_hotel_inventory(
            recommendation_id="hotel-rec",
            hotel_name="Sample House",
            check_in_date=date(2026, 9, 12),
            check_out_date=date(2026, 9, 14),
            adults=2,
        )
        flight = await provider.search_flight_inventory(
            recommendation_id="flight-rec",
            origin="Mumbai",
            destination="Tokyo",
            departure_date=date(2026, 9, 12),
            return_date=None,
            adults=2,
        )
        hotel_quote = await provider.revalidate_hotel_rate(
            hotel.rooms[0].rate_plans[0].id
        )
        flight_quote = await provider.revalidate_flight_offer(flight.offers[0].id)
        return hotel, flight, hotel_quote, flight_quote

    hotel, flight, hotel_quote, flight_quote = run(scenario())

    assert provider.name == "checkin-demo"
    assert provider.source_mode == SourceMode.DEMO
    assert provider.is_live is False
    assert hotel.source_mode == SourceMode.DEMO
    assert hotel.is_live is False
    assert hotel.source_metadata["demo"] is True
    assert "not a supplier reservation" in hotel.source_metadata["notice"]
    assert all(
        rate.source_mode == SourceMode.DEMO
        and rate.is_live is False
        and rate.provider_metadata["demo"] is True
        and "not bookable inventory" in rate.description
        for room in hotel.rooms
        for rate in room.rate_plans
    )
    assert flight.source_mode == SourceMode.DEMO
    assert flight.is_live is False
    assert all(
        offer.source_mode == SourceMode.DEMO
        and offer.is_live is False
        and offer.source_metadata["demo"] is True
        for offer in flight.offers
    )
    assert hotel_quote.source_mode == SourceMode.DEMO
    assert hotel_quote.is_live is False
    assert hotel_quote.raw_status == "demo_quote"
    assert flight_quote.source_mode == SourceMode.DEMO
    assert flight_quote.is_live is False
    assert flight_quote.raw_status == "demo_quote"


def test_unavailable_provider_and_unknown_demo_ids_fail_closed():
    unavailable = UnavailableProvider()

    with pytest.raises(ProviderConfigurationError):
        run(unavailable.search_hotel_inventory())
    with pytest.raises(ProviderConfigurationError):
        run(unavailable.search_flight_inventory())
    with pytest.raises(ProviderConfigurationError):
        run(unavailable.revalidate_hotel_rate("rate"))
    with pytest.raises(ProviderConfigurationError):
        run(unavailable.revalidate_flight_offer("offer"))

    demo = DemoProvider(clock=lambda: NOW)
    with pytest.raises(ProviderItemUnavailableError):
        run(demo.revalidate_hotel_rate("unknown-rate"))
    with pytest.raises(ProviderItemUnavailableError):
        run(demo.revalidate_flight_offer("unknown-offer"))
