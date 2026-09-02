"""Trip-owned inventory searches and an honest, supplier-backed saved cart.

The saved-cart lifetime is deliberately independent from supplier quote and
hold expiries. Saving an item never claims that inventory has been reserved.
"""

from __future__ import annotations

import logging
import math
import os
import inspect
from datetime import datetime, timedelta, timezone
from typing import Callable

import db
from schemas import Recommendation, TripState

from .models import (
    AddCartItemInput,
    AvailabilityStatus,
    Cart,
    CartItem,
    CartItemKind,
    CartItemState,
    CartState,
    FlightInventory,
    FlightOffer,
    HotelInventory,
    Money,
    ProviderQuote,
    RoomRate,
    RoomType,
    SourceMode,
)
from .providers import (
    DemoProvider,
    DuffelProvider,
    InventoryProvider,
    InventoryProviderError,
    ProviderConfigurationError,
    ProviderItemUnavailableError,
    UnavailableProvider,
)

logger = logging.getLogger(__name__)

HOTEL_SNAPSHOTS = "hotels"
FLIGHT_SNAPSHOTS = "flights"
DEFAULT_CART_TTL_MINUTES = 60
EXACT_KINDS = {CartItemKind.HOTEL, CartItemKind.FLIGHT}
TERMINAL_STATES = {CartItemState.CONFIRMED, CartItemState.BOOKED}


class InventoryDomainError(RuntimeError):
    """A safe domain failure that an API route can expose to the user."""

    code: str | None = None

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class CartVersionConflict(InventoryDomainError):
    """The caller's cart snapshot is older than the stored cart."""

    code = "CART_VERSION_CONFLICT"

    def __init__(self) -> None:
        super().__init__("Your cart changed elsewhere. Refresh it and try again.", status_code=409)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recommendation(state: TripState, recommendation_id: str, category: str) -> Recommendation:
    for result in state.research_results or []:
        for item in result.recommendations:
            if item.id == recommendation_id and item.category == category:
                return item
    raise InventoryDomainError(
        f"No {category} recommendation with that ID belongs to this trip.",
        status_code=404,
    )


def _snapshot(raw: dict, bucket: str, recommendation_id: str) -> dict | None:
    inventory = raw.get("inventory_snapshots")
    if not isinstance(inventory, dict):
        return None
    values = inventory.get(bucket)
    if not isinstance(values, dict):
        return None
    value = values.get(recommendation_id)
    return value if isinstance(value, dict) else None


def _save_snapshot(trip_id: str, bucket: str, recommendation_id: str, value: dict) -> None:
    def update(raw: dict) -> None:
        snapshots = dict(raw.get("inventory_snapshots") or {})
        bucket_values = dict(snapshots.get(bucket) or {})
        bucket_values[recommendation_id] = value
        snapshots[bucket] = bucket_values
        raw["inventory_snapshots"] = snapshots

    db.mutate_trip_state(trip_id, update)


def _cart_from_raw(trip_id: str, raw: dict | None) -> Cart:
    payload = raw.get("cart") if isinstance(raw, dict) else None
    if isinstance(payload, dict):
        try:
            return Cart.model_validate(payload)
        except ValueError:
            logger.warning("Discarding invalid persisted cart for trip %s", trip_id)
    return Cart(trip_id=trip_id)


def _exact_choices(cart: Cart) -> set[str]:
    return {
        f"{item.kind.value}:{item.recommendation_id}:{item.rate_plan_id}"
        for item in cart.items
        if item.kind in EXACT_KINDS and item.rate_plan_id
    }


def _mutate_cart(
    trip_id: str,
    apply: Callable[[Cart, dict], None],
    *,
    invalidate_itinerary: bool = True,
) -> Cart:
    """Load, change, version, and persist the cart inside one locked trip mutation.

    Every read-modify-write happens under the row lock so two concurrent
    mutations can never overwrite each other. A change to the exact supplier
    choices drops any itinerary that was built from the previous choices.
    """
    result: list[Cart] = []

    def update(raw: dict) -> None:
        cart = _cart_from_raw(trip_id, raw)
        before = _exact_choices(cart)
        apply(cart, raw)
        cart.version += 1
        if invalidate_itinerary and _exact_choices(cart) != before:
            raw["itinerary"] = None
            raw["itinerary_fingerprint"] = None
        raw["cart"] = cart.model_dump(mode="json", by_alias=False)
        result.append(cart)

    db.mutate_trip_state(trip_id, update)
    return result[0]


def _money_text(value: Money | None) -> str | None:
    if value is None:
        return None
    return f"{value.amount:.2f} {value.currency}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _find_rate(inventory: HotelInventory, rate_plan_id: str) -> tuple[RoomType, RoomRate] | None:
    for room in inventory.rooms:
        for rate in room.rate_plans:
            if rate.id == rate_plan_id:
                return room, rate
    return None


def _hotel_choice(raw: dict, item: dict) -> dict:
    choice = {
        "kind": "hotel",
        "recommendation_id": item.get("recommendation_id"),
        "name": item.get("title"),
        "saved_option": item.get("subtitle"),
        "total": None,
    }
    total = item.get("total")
    if isinstance(total, dict) and total.get("amount") is not None:
        choice["total"] = f"{float(total['amount']):.2f} {total.get('currency', '')}".strip()
    snapshot = _snapshot(raw, HOTEL_SNAPSHOTS, str(item.get("recommendation_id")))
    if snapshot is None:
        return choice
    try:
        inventory = HotelInventory.model_validate(snapshot)
    except ValueError:
        return choice
    found = _find_rate(inventory, str(item.get("rate_plan_id")))
    if found is None:
        return choice
    room, rate = found
    choice.update({
        "name": inventory.hotel_name,
        "room": room.name,
        "room_description": room.description,
        "rate": rate.label,
        "board": rate.board,
        "refundable": rate.refundable,
        "cancellation": rate.cancellation_summary,
        "total": _money_text(rate.total),
        "nightly": _money_text(rate.nightly),
        "check_in": inventory.check_in_date.isoformat(),
        "check_out": inventory.check_out_date.isoformat(),
    })
    return choice


def _flight_choice(raw: dict, item: dict) -> dict:
    choice = {
        "kind": "flight",
        "recommendation_id": item.get("recommendation_id"),
        "name": item.get("title"),
        "saved_option": item.get("subtitle"),
        "total": None,
    }
    total = item.get("total")
    if isinstance(total, dict) and total.get("amount") is not None:
        choice["total"] = f"{float(total['amount']):.2f} {total.get('currency', '')}".strip()
    snapshot = _snapshot(raw, FLIGHT_SNAPSHOTS, str(item.get("recommendation_id")))
    if snapshot is None:
        return choice
    try:
        inventory = FlightInventory.model_validate(snapshot)
    except ValueError:
        return choice
    offer: FlightOffer | None = next(
        (value for value in inventory.offers if value.id == item.get("rate_plan_id")), None
    )
    if offer is None:
        return choice
    choice.update({
        "carrier": offer.carrier,
        "flight_number": offer.flight_number,
        "route": f"{offer.origin} -> {offer.destination}",
        "depart_at": _iso(offer.depart_at),
        "arrive_at": _iso(offer.arrive_at),
        "duration_minutes": offer.duration_minutes,
        "stops": offer.stops,
        "journey_type": offer.journey_type,
        "total": _money_text(offer.total),
    })
    return_carrier = getattr(offer, "return_carrier", None)
    return_origin = getattr(offer, "return_origin", None)
    return_destination = getattr(offer, "return_destination", None)
    if offer.journey_type == "round_trip":
        if return_carrier or return_origin:
            choice["return_leg"] = {
                "carrier": return_carrier,
                "flight_number": getattr(offer, "return_flight_number", None),
                "route": f"{return_origin} -> {return_destination}",
                "depart_at": _iso(getattr(offer, "return_depart_at", None)),
                "arrive_at": _iso(getattr(offer, "return_arrive_at", None)),
                "duration_minutes": getattr(offer, "return_duration_minutes", None),
                "stops": getattr(offer, "return_stops", None),
            }
        else:
            choice["return_leg"] = "unknown - do not invent a return flight"
    return choice


def exact_cart_choices(raw: dict) -> list[dict]:
    """Resolve saved hotel rates and flight offers to their supplier snapshots.

    The itinerary prompt receives these so the plan uses the exact room and
    flight the traveler saved instead of a generic substitute.
    """
    cart = raw.get("cart")
    items = cart.get("items") if isinstance(cart, dict) else None
    choices: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("rate_plan_id"):
            continue
        if item.get("kind") == CartItemKind.HOTEL.value:
            choices.append(_hotel_choice(raw, item))
        elif item.get("kind") == CartItemKind.FLIGHT.value:
            choices.append(_flight_choice(raw, item))
    return choices


def _money_changed(before: Money | None, after: Money) -> bool:
    return before is None or before.currency != after.currency or not math.isclose(
        before.amount, after.amount, abs_tol=0.009
    )


class InventoryService:
    """Coordinates one normalized supplier provider with durable trip state."""

    def __init__(
        self,
        provider: InventoryProvider,
        *,
        cart_ttl_minutes: int = DEFAULT_CART_TTL_MINUTES,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if cart_ttl_minutes < 1:
            raise ValueError("cart_ttl_minutes must be positive")
        self.provider = provider
        self.cart_ttl = timedelta(minutes=cart_ttl_minutes)
        self.clock = clock

    async def hotel_rates(self, state: TripState, recommendation_id: str) -> HotelInventory:
        recommendation = _recommendation(state, recommendation_id, "hotel")
        lookup_name = recommendation.metadata.get("booking_lookup_name")
        hotel_name = lookup_name.strip() if isinstance(lookup_name, str) and lookup_name.strip() else recommendation.name
        check_out_date = state.preferences.end_date
        if check_out_date <= state.preferences.start_date:
            check_out_date = state.preferences.start_date + timedelta(days=1)
        inventory = await self.provider.search_hotel_inventory(
            recommendation_id=recommendation.id,
            hotel_name=hotel_name,
            check_in_date=state.preferences.start_date,
            check_out_date=check_out_date,
            adults=state.preferences.num_travelers,
            children=0,
            rooms=max(1, math.ceil(state.preferences.num_travelers / 2)),
        )
        _save_snapshot(
            state.trip_id,
            HOTEL_SNAPSHOTS,
            recommendation.id,
            inventory.model_dump(mode="json", by_alias=False),
        )
        return inventory

    async def flight_offers(self, state: TripState, recommendation_id: str) -> FlightInventory:
        recommendation = _recommendation(state, recommendation_id, "transport")
        inventory = await self.provider.search_flight_inventory(
            recommendation_id=recommendation.id,
            origin=state.preferences.origin,
            destination=state.preferences.destination,
            departure_date=state.preferences.start_date,
            return_date=state.preferences.end_date,
            adults=state.preferences.num_travelers,
            limit=3,
        )
        _save_snapshot(
            state.trip_id,
            FLIGHT_SNAPSHOTS,
            recommendation.id,
            inventory.model_dump(mode="json", by_alias=False),
        )
        return inventory

    def cart(self, state: TripState) -> Cart:
        cart = _cart_from_raw(state.trip_id, db.load_trip_state(state.trip_id))
        now = self.clock()
        if not self._apply_expiry(cart, now):
            return cart
        return _mutate_cart(
            state.trip_id,
            lambda fresh, _raw: self._apply_expiry(fresh, now),
            invalidate_itinerary=False,
        )

    def _apply_expiry(self, cart: Cart, now: datetime) -> bool:
        """Expire the saved shortlist or individual supplier clocks; returns whether anything changed."""
        saved_expiry = _aware(cart.saved_expires_at)
        if saved_expiry is not None and saved_expiry <= now:
            cart.items = []
            cart.saved_expires_at = None
            self._finish_cart(cart, now)
            return True
        changed = False
        for item in cart.items:
            if item.status in TERMINAL_STATES:
                continue
            expiry = _aware(item.hold_expires_at) or _aware(item.quote_expires_at)
            if expiry is not None and expiry <= now and item.status != CartItemState.EXPIRED:
                item.status = CartItemState.EXPIRED
                item.message = "This supplier price expired. Recheck it before continuing."
                changed = True
        if changed:
            self._finish_cart(cart, now)
        return changed

    async def add_item(self, state: TripState, body: AddCartItemInput) -> Cart:
        category = {
            CartItemKind.HOTEL: "hotel",
            CartItemKind.FLIGHT: "transport",
            CartItemKind.RIDE: "transport",
            CartItemKind.RESTAURANT: "restaurant",
        }[body.kind]
        recommendation = _recommendation(state, body.recommendation_id, category)
        now = self.clock()

        def apply(cart: Cart, raw: dict) -> None:
            cart.items = [
                item for item in cart.items
                if not (item.kind == body.kind and item.recommendation_id == recommendation.id)
            ]
            cart.items.append(self._build_item(raw, body, recommendation, now))
            cart.saved_expires_at = now + self.cart_ttl
            self._finish_cart(cart, now)

        return _mutate_cart(state.trip_id, apply)

    def _build_item(
        self,
        raw: dict,
        body: AddCartItemInput,
        recommendation: Recommendation,
        now: datetime,
    ) -> CartItem:
        if body.kind == CartItemKind.HOTEL:
            snapshot = _snapshot(raw, HOTEL_SNAPSHOTS, recommendation.id)
            if snapshot is None:
                raise InventoryDomainError("Check this hotel's current room prices before saving one.")
            found = _find_rate(HotelInventory.model_validate(snapshot), body.rate_plan_id or "")
            if found is None:
                raise InventoryDomainError("That room rate is not part of this trip's latest supplier results.")
            room, rate = found
            self._require_addable(rate.availability_status, rate.quote_expires_at, rate.hold_expires_at)
            return CartItem(
                kind=body.kind,
                recommendation_id=recommendation.id,
                rate_plan_id=rate.id,
                title=recommendation.name,
                subtitle=f"{room.name} · {rate.label}",
                status=CartItemState.HELD if rate.hold_expires_at else CartItemState.QUOTED,
                total=rate.total,
                original_total=rate.total,
                quote_expires_at=rate.quote_expires_at,
                hold_expires_at=rate.hold_expires_at,
                source=rate.source,
                source_mode=rate.source_mode,
                is_live=rate.is_live,
                added_at=now,
                checked_at=now,
                message=(
                    "Supplier inventory is held until the hold timer expires."
                    if rate.hold_expires_at
                    else "Price saved, not reserved. Availability is rechecked before booking."
                ),
            )
        if body.kind == CartItemKind.FLIGHT:
            snapshot = _snapshot(raw, FLIGHT_SNAPSHOTS, recommendation.id)
            if snapshot is None:
                raise InventoryDomainError("Check current flight offers before saving one.")
            inventory = FlightInventory.model_validate(snapshot)
            offer = next((value for value in inventory.offers if value.id == body.rate_plan_id), None)
            if offer is None:
                raise InventoryDomainError("That flight is not part of this trip's latest supplier results.")
            self._require_addable(offer.availability_status, offer.quote_expires_at, offer.hold_expires_at)
            flight_number = f" {offer.flight_number}" if offer.flight_number else ""
            return CartItem(
                kind=body.kind,
                recommendation_id=recommendation.id,
                rate_plan_id=offer.id,
                title=f"{offer.carrier}{flight_number}",
                subtitle=f"{offer.origin} → {offer.destination}",
                status=CartItemState.HELD if offer.hold_expires_at else CartItemState.QUOTED,
                total=offer.total,
                original_total=offer.total,
                quote_expires_at=offer.quote_expires_at,
                hold_expires_at=offer.hold_expires_at,
                source=offer.source,
                source_mode=offer.source_mode,
                is_live=offer.is_live,
                added_at=now,
                checked_at=now,
                message=(
                    "Supplier inventory is held until the hold timer expires."
                    if offer.hold_expires_at
                    else "Fare saved, not reserved. Availability is rechecked before booking."
                ),
            )
        return CartItem(
            kind=body.kind,
            recommendation_id=recommendation.id,
            title=recommendation.name,
            subtitle=recommendation.estimated_cost,
            status=CartItemState.SAVED,
            source="CheckIn recommendation",
            source_mode=SourceMode.UNAVAILABLE,
            is_live=False,
            added_at=now,
            checked_at=now,
            message="Saved choice only. No supplier inventory or booking hold is attached.",
        )

    def remove_item(self, state: TripState, item_id: str, *, expected_version: int | None = None) -> Cart:
        now = self.clock()

        def apply(cart: Cart, _raw: dict) -> None:
            if expected_version is not None and cart.version != expected_version:
                raise CartVersionConflict()
            remaining = [item for item in cart.items if item.id != item_id]
            if len(remaining) == len(cart.items):
                raise InventoryDomainError("That cart item does not exist.", status_code=404)
            cart.items = remaining
            if not remaining:
                cart.saved_expires_at = None
            self._finish_cart(cart, now)

        return _mutate_cart(state.trip_id, apply)

    async def revalidate(self, state: TripState) -> Cart:
        snapshot = self.cart(state)
        outcomes: dict[str, tuple[str, ProviderQuote | None]] = {}
        for item in snapshot.items:
            if item.kind not in EXACT_KINDS or item.status in TERMINAL_STATES:
                continue
            try:
                quote = await (
                    self.provider.revalidate_hotel_rate(item.rate_plan_id or "")
                    if item.kind == CartItemKind.HOTEL
                    else self.provider.revalidate_flight_offer(item.rate_plan_id or "")
                )
                outcomes[item.id] = ("quote", quote)
            except ProviderItemUnavailableError:
                outcomes[item.id] = ("unavailable", None)
            except InventoryProviderError:
                outcomes[item.id] = ("error", None)
            except Exception:
                logger.exception("Unexpected inventory revalidation failure for cart item %s", item.id)
                outcomes[item.id] = ("error", None)

        def apply(cart: Cart, _raw: dict) -> None:
            now = self.clock()
            for item in cart.items:
                if item.kind not in EXACT_KINDS:
                    item.status = CartItemState.SAVED
                    item.checked_at = now
                    continue
                if item.status in TERMINAL_STATES:
                    continue
                outcome = outcomes.get(item.id)
                if outcome is None:
                    continue
                verdict, quote = outcome
                if verdict == "quote" and quote is not None:
                    self._apply_quote(item, quote)
                elif verdict == "unavailable":
                    item.status = CartItemState.UNAVAILABLE
                    item.checked_at = now
                    item.message = "The supplier says this option is no longer available."
                else:
                    item.status = CartItemState.ERROR
                    item.checked_at = now
                    item.message = "The supplier could not recheck this price. Try again shortly."
            self._finish_cart(cart, now)

        return _mutate_cart(state.trip_id, apply)

    def _require_addable(
        self,
        status: AvailabilityStatus,
        quote_expires_at: datetime | None,
        hold_expires_at: datetime | None,
    ) -> None:
        if status not in {AvailabilityStatus.AVAILABLE, AvailabilityStatus.LIMITED}:
            raise InventoryDomainError("That supplier option is no longer available. Refresh prices.")
        expiry = _aware(hold_expires_at) or _aware(quote_expires_at)
        if expiry is not None and expiry <= self.clock():
            raise InventoryDomainError("That supplier price expired. Refresh prices before saving it.")

    @staticmethod
    def _apply_quote(item: CartItem, quote: ProviderQuote) -> None:
        previous = item.total
        item.provider_quote_id = quote.provider_reference
        item.total = quote.total
        item.quote_expires_at = quote.quote_expires_at
        item.hold_expires_at = quote.hold_expires_at
        item.source = quote.source
        item.source_mode = quote.source_mode
        item.is_live = quote.is_live
        item.checked_at = quote.checked_at
        if not quote.available:
            item.status = CartItemState.UNAVAILABLE
            item.message = "The supplier says this option is no longer available."
        elif _money_changed(previous, quote.total):
            item.status = CartItemState.PRICE_CHANGED
            item.message = "The supplier returned a new price. Review it before continuing."
        elif quote.hold_expires_at:
            item.status = CartItemState.HELD
            item.message = "Supplier inventory is held until the hold timer expires."
        else:
            item.status = CartItemState.QUOTED
            item.message = "Supplier price rechecked. It is not reserved."

    @staticmethod
    def _finish_cart(cart: Cart, now: datetime) -> None:
        issue_states = {
            CartItemState.PRICE_CHANGED,
            CartItemState.UNAVAILABLE,
            CartItemState.EXPIRED,
            CartItemState.ERROR,
        }
        if not cart.items:
            cart.state = CartState.OPEN
        elif all(item.status in TERMINAL_STATES for item in cart.items):
            cart.state = CartState.CONFIRMED
        elif any(item.status in issue_states for item in cart.items):
            cart.state = CartState.PARTIAL
        elif all(item.status in {CartItemState.QUOTED, CartItemState.HELD} for item in cart.items):
            cart.state = CartState.READY
        else:
            cart.state = CartState.OPEN
        holds = [
            _aware(item.hold_expires_at)
            for item in cart.items
            if item.status == CartItemState.HELD and _aware(item.hold_expires_at) is not None
        ]
        cart.earliest_hold_expires_at = min(holds) if holds else None
        cart.checked_at = now


_inventory_service: InventoryService | None = None


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s; using %d", name, default)
        return default


def _build_provider() -> InventoryProvider:
    selected = os.getenv("INVENTORY_PROVIDER", "unavailable").strip().lower()
    if selected == "demo":
        return DemoProvider()
    if selected == "duffel":
        mode_name = os.getenv("DUFFEL_MODE", "test").strip().lower()
        mode = SourceMode.LIVE if mode_name == "live" else SourceMode.TEST
        try:
            timeout = float(os.getenv("INVENTORY_HTTP_TIMEOUT_SECONDS", "25"))
        except ValueError:
            timeout = 25.0
        return DuffelProvider(
            os.getenv("DUFFEL_ACCESS_TOKEN", ""),
            source_mode=mode,
            base_url=os.getenv("DUFFEL_API_BASE_URL", "https://api.duffel.com"),
            timeout_seconds=timeout,
        )
    if selected not in {"", "unavailable"}:
        logger.warning("Unknown INVENTORY_PROVIDER=%s; inventory disabled", selected)
    return UnavailableProvider()


def get_inventory_service() -> InventoryService:
    global _inventory_service
    if _inventory_service is None:
        try:
            provider = _build_provider()
        except ProviderConfigurationError as exc:
            logger.warning("Inventory disabled: %s", exc)
            provider = UnavailableProvider()
        _inventory_service = InventoryService(
            provider,
            cart_ttl_minutes=_int_env("CART_TTL_MINUTES", DEFAULT_CART_TTL_MINUTES),
        )
    return _inventory_service


def reset_inventory_service() -> None:
    """Clear the singleton after environment changes in tests or local tools."""
    global _inventory_service
    _inventory_service = None


async def close_inventory_service() -> None:
    """Close provider network resources during application shutdown."""
    global _inventory_service
    service = _inventory_service
    _inventory_service = None
    if service is None:
        return
    close = getattr(service.provider, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result
