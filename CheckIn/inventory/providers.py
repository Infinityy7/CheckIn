"""Supplier adapters for hotel and flight inventory.

The public protocol returns only normalized domain models. Supplier payloads are
treated as untrusted: malformed values fail closed instead of being displayed
as live availability. Demo inventory is opt-in and is always labelled non-live.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

from .models import (
    AvailabilityStatus,
    Bed,
    CancellationPolicy,
    CancellationWindow,
    FlightInventory,
    FlightOffer,
    HotelInventory,
    Money,
    Occupancy,
    ProviderQuote,
    RoomRate,
    RoomType,
    SourceMode,
)


class InventoryProviderError(RuntimeError):
    """Safe, classified supplier failure suitable for mapping to an API error."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ProviderConfigurationError(InventoryProviderError):
    def __init__(self, message: str = "Live inventory is not configured.") -> None:
        super().__init__(message, code="INVENTORY_NOT_CONFIGURED", retryable=False)


class ProviderUnavailableError(InventoryProviderError):
    def __init__(self, message: str = "The inventory provider is temporarily unavailable.") -> None:
        super().__init__(message, code="INVENTORY_PROVIDER_UNAVAILABLE", retryable=True)


class ProviderItemUnavailableError(InventoryProviderError):
    def __init__(self, message: str = "That option is no longer available.") -> None:
        super().__init__(message, code="INVENTORY_ITEM_UNAVAILABLE", retryable=False)


class ProviderDataError(InventoryProviderError):
    def __init__(self, message: str = "The inventory provider returned incomplete data.") -> None:
        super().__init__(message, code="INVENTORY_PROVIDER_DATA_INVALID", retryable=True)


@runtime_checkable
class InventoryProvider(Protocol):
    """Provider-independent search and price revalidation boundary."""

    name: str
    source_mode: SourceMode
    is_live: bool

    async def search_hotel_inventory(
        self,
        *,
        recommendation_id: str,
        hotel_name: str,
        check_in_date: date,
        check_out_date: date,
        adults: int,
        children: int = 0,
        rooms: int = 1,
    ) -> HotelInventory: ...

    async def search_flight_inventory(
        self,
        *,
        recommendation_id: str,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
        adults: int,
        cabin_class: str = "economy",
        limit: int = 3,
    ) -> FlightInventory: ...

    async def revalidate_hotel_rate(self, rate_plan_id: str) -> ProviderQuote: ...

    async def revalidate_flight_offer(self, offer_id: str) -> ProviderQuote: ...


class UnavailableProvider:
    """Fail-closed provider used when no supplier is explicitly configured."""

    name = "unavailable"
    source_mode = SourceMode.UNAVAILABLE
    is_live = False

    async def search_hotel_inventory(self, **_: Any) -> HotelInventory:
        raise ProviderConfigurationError()

    async def search_flight_inventory(self, **_: Any) -> FlightInventory:
        raise ProviderConfigurationError()

    async def revalidate_hotel_rate(self, _rate_plan_id: str) -> ProviderQuote:
        raise ProviderConfigurationError()

    async def revalidate_flight_offer(self, _offer_id: str) -> ProviderQuote:
        raise ProviderConfigurationError()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DemoProvider:
    """Deterministic opt-in sample inventory. It never claims to be live."""

    name = "checkin-demo"
    source_mode = SourceMode.DEMO
    is_live = False

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._hotel_rates: dict[str, Money] = {}
        self._flight_offers: dict[str, Money] = {}

    @staticmethod
    def _stable_number(value: str, low: int, high: int) -> int:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return low + int.from_bytes(digest[:4], "big") % (high - low + 1)

    async def search_hotel_inventory(
        self,
        *,
        recommendation_id: str,
        hotel_name: str,
        check_in_date: date,
        check_out_date: date,
        adults: int,
        children: int = 0,
        rooms: int = 1,
    ) -> HotelInventory:
        if check_out_date <= check_in_date:
            raise ValueError("check-out must be after check-in")
        if adults < 1 or rooms < 1:
            raise ValueError("at least one adult and one room are required")
        nights = (check_out_date - check_in_date).days
        now = self._clock()
        expires_at = now + timedelta(hours=1)
        room_specs = (
            ("city", "City Room", "A quiet room with one queen bed.", [Bed(type="queen", count=1)], 1.0),
            ("view", "View Room", "A larger room with a destination-facing view.", [Bed(type="king", count=1)], 1.28),
            ("suite", "Explorer Suite", "Separate living space for longer stays.", [Bed(type="king", count=1)], 1.62),
        )
        room_types: list[RoomType] = []
        base_nightly = self._stable_number(f"{hotel_name}:{check_in_date}", 105, 245)
        max_guests = max(adults + children, 2)
        for room_index, (slug, name, description, beds, multiplier) in enumerate(room_specs):
            plans: list[RoomRate] = []
            for plan_index, (plan_slug, label, refundable, board, uplift) in enumerate((
                ("basic", "Room only", False, "room_only", 1.0),
                ("flex", "Flexible + breakfast", True, "breakfast_included", 1.16),
            )):
                nightly_amount = round(base_nightly * multiplier * uplift, 2)
                base_amount = round(nightly_amount * nights * rooms, 2)
                taxes_amount = round(base_amount * 0.12, 2)
                total_amount = round(base_amount + taxes_amount, 2)
                rate_id = f"demo-hotel-{self._stable_number(f'{recommendation_id}:{slug}:{plan_slug}', 100000, 999999)}"
                total = Money(amount=total_amount, currency="USD")
                self._hotel_rates[rate_id] = total
                timeline = []
                if refundable:
                    timeline = [CancellationWindow(
                        before=datetime.combine(
                            check_in_date - timedelta(days=2), time(18, 0), tzinfo=timezone.utc
                        ),
                        refund=total,
                    )]
                summary = (
                    "Free cancellation until two days before check-in."
                    if refundable else "Non-refundable after booking."
                )
                remaining = max(1, 5 - room_index - plan_index)
                plans.append(RoomRate(
                    id=rate_id,
                    label=label,
                    description=(
                        "Sample rate for interface testing only; not bookable inventory."
                    ),
                    base=Money(amount=base_amount, currency="USD"),
                    total=total,
                    nightly=Money(amount=nightly_amount, currency="USD"),
                    taxes_and_fees=Money(amount=taxes_amount, currency="USD"),
                    refundable=refundable,
                    cancellation_summary=summary,
                    cancellation=CancellationPolicy(
                        refundable=refundable,
                        summary=summary,
                        timeline=timeline,
                    ),
                    board=board,
                    payment_type="demo_only",
                    availability_status=(
                        AvailabilityStatus.LIMITED if remaining <= 2 else AvailabilityStatus.AVAILABLE
                    ),
                    rooms_remaining=remaining,
                    quote_expires_at=expires_at,
                    source=self.name,
                    source_mode=self.source_mode,
                    is_live=False,
                    provider_metadata={"demo": True},
                ))
            room_types.append(RoomType(
                id=f"demo-room-{slug}",
                name=name,
                description=description,
                occupancy=Occupancy(adults=adults, children=children, max_guests=max_guests),
                beds=beds,
                board="varies_by_rate",
                rate_plans=plans,
            ))
        return HotelInventory(
            hotel_id=f"demo-hotel-{self._stable_number(hotel_name, 1000, 9999)}",
            recommendation_id=recommendation_id,
            hotel_name=hotel_name,
            source=self.name,
            source_mode=self.source_mode,
            is_live=False,
            checked_at=now,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            rooms=room_types,
            source_metadata={
                "demo": True,
                "notice": "Sample inventory for UI testing; not a supplier reservation.",
            },
        )

    async def search_flight_inventory(
        self,
        *,
        recommendation_id: str,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
        adults: int,
        cabin_class: str = "economy",
        limit: int = 3,
    ) -> FlightInventory:
        if adults < 1:
            raise ValueError("at least one adult is required")
        now = self._clock()
        offers: list[FlightOffer] = []
        origin_code = _demo_airport_code(origin)
        destination_code = _demo_airport_code(destination)
        for index in range(max(0, min(limit, 3))):
            depart_at = datetime.combine(
                departure_date, time(8 + index * 3, 15), tzinfo=timezone.utc
            )
            duration_minutes = 150 + self._stable_number(
                f"{origin}:{destination}:{index}", 0, 420
            )
            arrive_at = depart_at + timedelta(minutes=duration_minutes)
            amount = float(
                self._stable_number(f"{origin}:{destination}:{departure_date}:{index}", 180, 720)
                * adults
                * (2 if return_date else 1)
            )
            offer_id = f"demo-flight-{self._stable_number(f'{recommendation_id}:{index}', 100000, 999999)}"
            total = Money(amount=amount, currency="USD")
            self._flight_offers[offer_id] = total
            carrier = ("Atlas Demo Air", "Northstar Demo", "Wayfinder Demo")[index]
            return_leg: dict[str, Any] = {}
            if return_date is not None:
                return_depart_at = datetime.combine(
                    return_date, time(10 + index * 3, 45), tzinfo=timezone.utc
                )
                return_duration = 150 + self._stable_number(
                    f"{destination}:{origin}:{index}", 0, 420
                )
                return_leg = {
                    "return_carrier": carrier,
                    "return_flight_number": f"DM{310 + index}",
                    "return_origin": destination_code,
                    "return_destination": origin_code,
                    "return_depart_at": return_depart_at,
                    "return_arrive_at": return_depart_at + timedelta(minutes=return_duration),
                    "return_duration_minutes": return_duration,
                    "return_stops": 0 if index == 0 else 1,
                }
            offers.append(FlightOffer(
                id=offer_id,
                carrier=carrier,
                flight_number=f"DM{210 + index}",
                origin=origin_code,
                destination=destination_code,
                depart_at=depart_at,
                arrive_at=arrive_at,
                duration_minutes=duration_minutes,
                stops=0 if index == 0 else 1,
                journey_type="round_trip" if return_date else "one_way",
                **return_leg,
                total=total,
                quote_expires_at=now + timedelta(minutes=30),
                availability_status=AvailabilityStatus.AVAILABLE,
                source=self.name,
                source_mode=self.source_mode,
                is_live=False,
                source_metadata={"demo": True, "cabin_class": cabin_class},
            ))
        return FlightInventory(
            recommendation_id=recommendation_id,
            source=self.name,
            source_mode=self.source_mode,
            is_live=False,
            checked_at=now,
            offers=offers,
        )

    async def revalidate_hotel_rate(self, rate_plan_id: str) -> ProviderQuote:
        total = self._hotel_rates.get(rate_plan_id)
        if total is None:
            raise ProviderItemUnavailableError("That demo room rate is no longer in this session.")
        now = self._clock()
        return ProviderQuote(
            provider_reference=rate_plan_id,
            total=total,
            available=True,
            checked_at=now,
            quote_expires_at=now + timedelta(hours=1),
            source=self.name,
            source_mode=self.source_mode,
            is_live=False,
            raw_status="demo_quote",
        )

    async def revalidate_flight_offer(self, offer_id: str) -> ProviderQuote:
        total = self._flight_offers.get(offer_id)
        if total is None:
            raise ProviderItemUnavailableError("That demo flight offer is no longer in this session.")
        now = self._clock()
        return ProviderQuote(
            provider_reference=offer_id,
            total=total,
            available=True,
            checked_at=now,
            quote_expires_at=now + timedelta(minutes=30),
            source=self.name,
            source_mode=self.source_mode,
            is_live=False,
            raw_status="demo_quote",
        )


class DuffelProvider:
    """Real Duffel Flights + Stays adapter with an injectable HTTP client."""

    name = "duffel"

    def __init__(
        self,
        access_token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        source_mode: SourceMode = SourceMode.TEST,
        base_url: str = "https://api.duffel.com",
        timeout_seconds: float = 25.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not access_token.strip():
            raise ProviderConfigurationError("DUFFEL_ACCESS_TOKEN is not configured.")
        if source_mode not in {SourceMode.LIVE, SourceMode.TEST}:
            raise ValueError("Duffel source_mode must be live or test")
        self.source_mode = source_mode
        self.is_live = source_mode == SourceMode.LIVE
        self._access_token = access_token.strip()
        self._client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._clock = clock

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Duffel-Version": "v2",
            "Authorization": f"Bearer {self._access_token}",
        }
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ProviderUnavailableError() from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError() from exc

        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise ProviderDataError("Duffel returned a non-JSON response.") from exc
        if not isinstance(payload, dict):
            raise ProviderDataError()

        status = int(getattr(response, "status_code", 500))
        if status < 400:
            return payload

        provider_code = _duffel_error_code(payload)
        if status in {401, 403}:
            raise ProviderConfigurationError("Duffel rejected the configured credentials or permissions.")
        if status in {404, 410} or provider_code in {
            "rate_unavailable", "result_no_longer_available", "offer_no_longer_available"
        }:
            raise ProviderItemUnavailableError()
        if status == 429 or status >= 500:
            raise ProviderUnavailableError()
        raise InventoryProviderError(
            "Duffel could not complete the inventory request.",
            code=f"DUFFEL_{provider_code.upper()}" if provider_code else "DUFFEL_REQUEST_REJECTED",
            retryable=False,
        )

    async def search_hotel_inventory(
        self,
        *,
        recommendation_id: str,
        hotel_name: str,
        check_in_date: date,
        check_out_date: date,
        adults: int,
        children: int = 0,
        rooms: int = 1,
    ) -> HotelInventory:
        if check_out_date <= check_in_date:
            raise ValueError("check-out must be after check-in")
        if adults < 1 or rooms < 1:
            raise ValueError("at least one adult and one room are required")
        suggestion_payload = await self._request(
            "POST",
            "/stays/accommodation/suggestions",
            body={"data": {"query": hotel_name}},
        )
        suggestions = _required_list(suggestion_payload, "data")
        if not suggestions:
            raise ProviderItemUnavailableError("No supplier property matched this hotel.")
        suggestion = _best_accommodation_match(suggestions, hotel_name)
        accommodation_id = _required_string(suggestion, "accommodation_id")
        guests = [{"type": "adult"} for _ in range(adults)] + [
            {"type": "child", "age": 8} for _ in range(children)
        ]
        search_payload = await self._request(
            "POST",
            "/stays/search",
            body={
                "data": {
                    "accommodation": {"ids": [accommodation_id], "fetch_rates": True},
                    "check_in_date": check_in_date.isoformat(),
                    "check_out_date": check_out_date.isoformat(),
                    "guests": guests,
                    "rooms": rooms,
                }
            },
        )
        search_data = _required_dict(search_payload, "data")
        results = _required_list(search_data, "results")
        result = _find_hotel_result(results, accommodation_id)
        accommodation = _optional_dict(result.get("accommodation"))
        if not _optional_list(accommodation.get("rooms")):
            result = await self.fetch_hotel_rates(_required_string(result, "id"))
        return _normalize_hotel_result(
            result,
            recommendation_id=recommendation_id,
            requested_name=hotel_name,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            adults=adults,
            children=children,
            source_mode=self.source_mode,
            checked_at=self._clock(),
        )

    async def fetch_hotel_rates(self, search_result_id: str) -> dict[str, Any]:
        safe_id = quote(search_result_id, safe="")
        payload = await self._request(
            "POST", f"/stays/search_results/{safe_id}/actions/fetch_all_rates"
        )
        return _required_dict(payload, "data")

    async def suggest_place(self, query: str) -> str:
        compact = query.strip().upper()
        if re.fullmatch(r"[A-Z]{3}", compact):
            return compact
        payload = await self._request(
            "GET", "/places/suggestions", params={"query": query.strip()}
        )
        places = _required_list(payload, "data")
        if not places:
            raise ProviderItemUnavailableError(f"No airport matched {query!r}.")
        code = places[0].get("iata_code") if isinstance(places[0], dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code.upper()):
            raise ProviderDataError("Duffel place suggestion did not contain an airport code.")
        return code.upper()

    async def search_flight_inventory(
        self,
        *,
        recommendation_id: str,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: date | None,
        adults: int,
        cabin_class: str = "economy",
        limit: int = 3,
    ) -> FlightInventory:
        if adults < 1:
            raise ValueError("at least one adult is required")
        origin_code, destination_code = await _gather_two(
            self.suggest_place(origin), self.suggest_place(destination)
        )
        slices = [{
            "origin": origin_code,
            "destination": destination_code,
            "departure_date": departure_date.isoformat(),
        }]
        if return_date is not None:
            if return_date < departure_date:
                raise ValueError("return date must not precede departure date")
            slices.append({
                "origin": destination_code,
                "destination": origin_code,
                "departure_date": return_date.isoformat(),
            })
        payload = await self._request(
            "POST",
            "/air/offer_requests",
            params={"return_offers": "true", "supplier_timeout": "15000"},
            body={
                "data": {
                    "slices": slices,
                    "passengers": [{"type": "adult"} for _ in range(adults)],
                    "cabin_class": cabin_class,
                }
            },
        )
        data = _required_dict(payload, "data")
        raw_offers = _required_list(data, "offers")
        live_mode = data.get("live_mode")
        mode = (
            SourceMode.LIVE if live_mode is True
            else SourceMode.TEST if live_mode is False
            else self.source_mode
        )
        offers = [
            _normalize_flight_offer(item, source_mode=mode)
            for item in raw_offers[: max(0, min(limit, 20))]
        ]
        return FlightInventory(
            recommendation_id=recommendation_id,
            source=self.name,
            source_mode=mode,
            is_live=mode == SourceMode.LIVE,
            checked_at=self._clock(),
            offers=offers,
        )

    async def revalidate_hotel_rate(self, rate_plan_id: str) -> ProviderQuote:
        payload = await self._request(
            "POST", "/stays/quotes", body={"data": {"rate_id": rate_plan_id}}
        )
        data = _required_dict(payload, "data")
        total = _money(data, "total_amount", "total_currency")
        return ProviderQuote(
            provider_reference=_required_string(data, "id"),
            total=total,
            available=True,
            checked_at=self._clock(),
            quote_expires_at=_optional_datetime(data.get("expires_at")),
            source=self.name,
            source_mode=self.source_mode,
            is_live=self.is_live,
            raw_status="quoted",
        )

    async def revalidate_flight_offer(self, offer_id: str) -> ProviderQuote:
        payload = await self._request("GET", f"/air/offers/{quote(offer_id, safe='')}")
        data = _required_dict(payload, "data")
        expires_at = _required_datetime(data, "expires_at")
        live_mode = data.get("live_mode")
        mode = (
            SourceMode.LIVE if live_mode is True
            else SourceMode.TEST if live_mode is False
            else self.source_mode
        )
        return ProviderQuote(
            provider_reference=_required_string(data, "id"),
            total=_money(data, "total_amount", "total_currency"),
            available=expires_at > self._clock(),
            checked_at=self._clock(),
            quote_expires_at=expires_at,
            source=self.name,
            source_mode=mode,
            is_live=mode == SourceMode.LIVE,
            raw_status="quoted",
        )


async def _gather_two(
    first: Awaitable[str], second: Awaitable[str]
) -> tuple[str, str]:
    import asyncio

    first_result, second_result = await asyncio.gather(first, second)
    return first_result, second_result


def _demo_airport_code(value: str) -> str:
    letters = "".join(character for character in value.upper() if character.isalpha())
    return (letters[:3] if len(letters) >= 3 else (letters + "XXX")[:3])


def _required_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ProviderDataError(f"Duffel response is missing {key}.")
    return value


def _optional_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _required_list(container: dict[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise ProviderDataError(f"Duffel response is missing {key}.")
    return value


def _optional_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required_string(container: dict[str, Any], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProviderDataError(f"Duffel response is missing {key}.")
    return value


def _required_datetime(container: dict[str, Any], key: str) -> datetime:
    value = _optional_datetime(container.get(key))
    if value is None:
        raise ProviderDataError(f"Duffel response is missing {key}.")
    return value


def _optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderDataError("Duffel returned an invalid timestamp.") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _number(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProviderDataError(f"Duffel returned an invalid {field}.") from exc
    if not math.isfinite(number) or number < 0:
        raise ProviderDataError(f"Duffel returned an invalid {field}.")
    return round(number, 2)


def _money(container: dict[str, Any], amount_key: str, currency_key: str) -> Money:
    currency = container.get(currency_key)
    if not isinstance(currency, str):
        raise ProviderDataError(f"Duffel response is missing {currency_key}.")
    return Money(amount=_number(container.get(amount_key), field=amount_key), currency=currency)


def _optional_money(
    container: dict[str, Any], amount_key: str, currency_key: str
) -> Money | None:
    if container.get(amount_key) is None:
        return None
    return _money(container, amount_key, currency_key)


def _duffel_error_code(payload: dict[str, Any]) -> str:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        code = errors[0].get("code")
        return code if isinstance(code, str) else ""
    return ""


def _best_accommodation_match(suggestions: list[Any], hotel_name: str) -> dict[str, Any]:
    valid = [item for item in suggestions if isinstance(item, dict)]
    if not valid:
        raise ProviderDataError("Duffel accommodation suggestions were malformed.")
    target = " ".join(hotel_name.casefold().split())
    for item in valid:
        candidate = item.get("accommodation_name")
        if isinstance(candidate, str) and " ".join(candidate.casefold().split()) == target:
            return item
    return valid[0]


def _find_hotel_result(results: list[Any], accommodation_id: str) -> dict[str, Any]:
    valid = [item for item in results if isinstance(item, dict)]
    for item in valid:
        accommodation = _optional_dict(item.get("accommodation"))
        if accommodation.get("id") == accommodation_id:
            return item
    if valid:
        return valid[0]
    raise ProviderItemUnavailableError("No available rooms matched this hotel and date range.")


def _normalize_hotel_result(
    result: dict[str, Any],
    *,
    recommendation_id: str,
    requested_name: str,
    check_in_date: date,
    check_out_date: date,
    adults: int,
    children: int,
    source_mode: SourceMode,
    checked_at: datetime,
) -> HotelInventory:
    accommodation = _required_dict(result, "accommodation")
    raw_rooms = _optional_list(accommodation.get("rooms"))
    nights = (check_out_date - check_in_date).days
    room_models: list[RoomType] = []
    for room_index, raw_room in enumerate(raw_rooms):
        if not isinstance(raw_room, dict):
            raise ProviderDataError("Duffel returned a malformed room.")
        rate_models: list[RoomRate] = []
        for raw_rate in _optional_list(raw_room.get("rates")):
            if not isinstance(raw_rate, dict):
                raise ProviderDataError("Duffel returned a malformed room rate.")
            total = _money(raw_rate, "total_amount", "total_currency")
            base = _optional_money(raw_rate, "base_amount", "base_currency")
            tax = _optional_money(raw_rate, "tax_amount", "tax_currency")
            fee = _optional_money(raw_rate, "fee_amount", "fee_currency")
            if tax and fee and tax.currency != fee.currency:
                raise ProviderDataError("Duffel returned mixed currencies for taxes and fees.")
            tax_currency = (tax or fee or total).currency
            taxes_amount = round((tax.amount if tax else 0) + (fee.amount if fee else 0), 2)
            timeline = _normalize_cancellation_timeline(
                raw_rate.get("cancellation_timeline"), total
            )
            refundable = any(item.refund.amount > 0 for item in timeline)
            cancellation_summary = _cancellation_summary(timeline, total)
            remaining_value = raw_rate.get("quantity_available")
            remaining = int(remaining_value) if isinstance(remaining_value, int) and remaining_value >= 0 else None
            status = (
                AvailabilityStatus.UNAVAILABLE if remaining == 0
                else AvailabilityStatus.LIMITED if remaining is not None and remaining <= 3
                else AvailabilityStatus.AVAILABLE
            )
            rate_models.append(RoomRate(
                id=_required_string(raw_rate, "id"),
                label=str(raw_rate.get("name") or "Available rate"),
                description=str(raw_rate.get("description") or ""),
                base=base,
                total=total,
                nightly=Money(amount=total.amount / max(1, nights), currency=total.currency),
                taxes_and_fees=Money(amount=taxes_amount, currency=tax_currency),
                due_at_property=_optional_money(
                    raw_rate, "due_at_accommodation_amount", "due_at_accommodation_currency"
                ),
                refundable=refundable,
                cancellation_summary=cancellation_summary,
                cancellation=CancellationPolicy(
                    refundable=refundable,
                    summary=cancellation_summary,
                    timeline=timeline,
                ),
                board=str(raw_rate.get("board_type") or "room_only"),
                payment_type=(
                    str(raw_rate["payment_type"]) if raw_rate.get("payment_type") else None
                ),
                availability_status=status,
                rooms_remaining=remaining,
                quote_expires_at=(
                    _optional_datetime(raw_rate.get("expires_at"))
                    or _optional_datetime(result.get("expires_at"))
                ),
                source="duffel",
                source_mode=source_mode,
                is_live=source_mode == SourceMode.LIVE,
                provider_metadata={
                    "search_result_id": result.get("id"),
                    "rate_id": raw_rate.get("id"),
                },
            ))
        if not rate_models:
            continue
        raw_beds = _optional_list(raw_room.get("beds"))
        beds = [
            Bed(type=str(item.get("type") or "bed"), count=int(item.get("count") or 1))
            for item in raw_beds if isinstance(item, dict)
        ]
        photos = [
            item["url"] for item in _optional_list(raw_room.get("photos"))
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        room_models.append(RoomType(
            id=str(raw_room.get("id") or f"{result.get('id', 'room')}:{room_index}"),
            name=str(raw_room.get("name") or f"Room {room_index + 1}"),
            description=str(raw_room.get("description") or ""),
            occupancy=Occupancy(
                adults=adults,
                children=children,
                max_guests=max(adults + children, 1),
            ),
            beds=beds,
            photos=photos,
            rate_plans=rate_models,
        ))
    if not room_models:
        raise ProviderItemUnavailableError("No bookable room rates remain for this hotel.")
    return HotelInventory(
        hotel_id=_required_string(accommodation, "id"),
        recommendation_id=recommendation_id,
        hotel_name=str(accommodation.get("name") or requested_name),
        source="duffel",
        source_mode=source_mode,
        is_live=source_mode == SourceMode.LIVE,
        checked_at=checked_at,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        rooms=room_models,
        source_metadata={"search_result_id": result.get("id")},
    )


def _normalize_cancellation_timeline(raw: Any, total: Money) -> list[CancellationWindow]:
    timeline: list[CancellationWindow] = []
    for item in _optional_list(raw):
        if not isinstance(item, dict):
            raise ProviderDataError("Duffel returned a malformed cancellation policy.")
        before = _required_datetime(item, "before")
        refund = _money(item, "refund_amount", "currency")
        if refund.currency != total.currency:
            raise ProviderDataError("Duffel returned mixed cancellation currencies.")
        timeline.append(CancellationWindow(before=before, refund=refund))
    return sorted(timeline, key=lambda item: item.before)


def _cancellation_summary(timeline: list[CancellationWindow], total: Money) -> str:
    if not timeline:
        return "Non-refundable."
    full = next((item for item in timeline if item.refund.amount >= total.amount), None)
    if full:
        return f"Full refund before {full.before.isoformat()}."
    positive = next((item for item in timeline if item.refund.amount > 0), None)
    if positive:
        return f"Partial refund before {positive.before.isoformat()}."
    return "Non-refundable."


def _normalize_flight_offer(raw: Any, *, source_mode: SourceMode) -> FlightOffer:
    if not isinstance(raw, dict):
        raise ProviderDataError("Duffel returned a malformed flight offer.")
    slices = _required_list(raw, "slices")
    outbound = _normalize_flight_slice(slices[0] if slices else None, leg="outbound")
    return_leg: dict[str, Any] = {}
    if len(slices) > 1:
        inbound = _normalize_flight_slice(slices[1], leg="return")
        return_leg = {f"return_{field}": value for field, value in inbound.items()}
    expires_at = _required_datetime(raw, "expires_at")
    now = datetime.now(timezone.utc)
    return FlightOffer(
        id=_required_string(raw, "id"),
        **outbound,
        journey_type="round_trip" if len(slices) > 1 else "one_way",
        **return_leg,
        total=_money(raw, "total_amount", "total_currency"),
        quote_expires_at=expires_at,
        availability_status=(
            AvailabilityStatus.EXPIRED if expires_at <= now else AvailabilityStatus.AVAILABLE
        ),
        source="duffel",
        source_mode=source_mode,
        is_live=source_mode == SourceMode.LIVE,
        source_metadata={
            "offer_request_id": raw.get("offer_request_id"),
            "requires_instant_payment": _optional_dict(
                raw.get("payment_requirements")
            ).get("requires_instant_payment"),
        },
    )


def _normalize_flight_slice(raw: Any, *, leg: str) -> dict[str, Any]:
    slice_data = raw if isinstance(raw, dict) else {}
    segments = [
        segment for segment in _optional_list(slice_data.get("segments"))
        if isinstance(segment, dict)
    ]
    if not segments:
        raise ProviderDataError(f"Duffel flight offer {leg} slice did not contain segments.")
    first, last = segments[0], segments[-1]
    depart_at = _required_datetime(first, "departing_at")
    arrive_at = _required_datetime(last, "arriving_at")
    operating = _optional_dict(first.get("operating_carrier"))
    marketing = _optional_dict(first.get("marketing_carrier"))
    operating_names = list(dict.fromkeys(
        str(_optional_dict(segment.get("operating_carrier")).get("name"))
        for segment in segments
        if _optional_dict(segment.get("operating_carrier")).get("name")
    ))
    carrier = " · ".join(operating_names) or str(
        operating.get("name") or marketing.get("name") or "Unknown carrier"
    )
    flight_number_value = first.get("marketing_carrier_flight_number")
    airline_code = marketing.get("iata_code") or operating.get("iata_code") or ""
    flight_number = (
        f"{airline_code}{flight_number_value}" if flight_number_value is not None else None
    )
    return {
        "carrier": carrier,
        "flight_number": flight_number,
        "origin": _place_code(first.get("origin")),
        "destination": _place_code(last.get("destination")),
        "depart_at": depart_at,
        "arrive_at": arrive_at,
        "duration_minutes": max(0, round((arrive_at - depart_at).total_seconds() / 60)),
        "stops": max(0, len(segments) - 1),
    }


def _place_code(raw: Any) -> str:
    place = _optional_dict(raw)
    code = place.get("iata_code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z]{3}", code.upper()):
        raise ProviderDataError("Duffel flight segment is missing an airport code.")
    return code.upper()
