"""Typed contracts for supplier inventory, quotes, carts, and transport legs."""

from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Use camelCase on the wire while accepting Python/snake_case internally."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class SourceMode(str, Enum):
    LIVE = "live"
    TEST = "test"
    DEMO = "demo"
    UNAVAILABLE = "unavailable"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"
    PRICE_CHANGED = "price_changed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class CartItemState(str, Enum):
    SAVED = "saved"
    QUOTED = "quoted"
    HELD = "held"
    REVALIDATING = "revalidating"
    BOOKING = "booking"
    # ``booked`` remains readable for older stored cart payloads. New code
    # emits the clearer supplier-final state, ``confirmed``.
    BOOKED = "booked"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    PRICE_CHANGED = "price_changed"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class CartState(str, Enum):
    OPEN = "open"
    REVALIDATING = "revalidating"
    READY = "ready"
    CHECKOUT = "checkout"
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    ERROR = "error"


class CartItemKind(str, Enum):
    HOTEL = "hotel"
    FLIGHT = "flight"
    RIDE = "ride"
    RESTAURANT = "restaurant"


class Money(ApiModel):
    amount: float = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3)

    @field_validator("amount")
    @classmethod
    def finite_amount(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("money amount must be finite")
        return round(value, 2)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("currency must be a three-letter ISO code")
        return value


class CancellationWindow(ApiModel):
    before: datetime
    refund: Money


class CancellationPolicy(ApiModel):
    refundable: bool
    summary: str
    timeline: list[CancellationWindow] = Field(default_factory=list)


class Occupancy(ApiModel):
    adults: int = Field(..., ge=1)
    children: int = Field(0, ge=0)
    max_guests: int = Field(..., ge=1)

    @model_validator(mode="after")
    def requested_party_fits(self) -> "Occupancy":
        if self.adults + self.children > self.max_guests:
            raise ValueError("requested guests exceed max_guests")
        return self


class Bed(ApiModel):
    type: str = Field(..., min_length=1)
    count: int = Field(..., ge=1)


class RoomRate(ApiModel):
    id: str
    label: str
    description: str = ""
    base: Money | None = None
    total: Money
    nightly: Money
    taxes_and_fees: Money
    due_at_property: Money | None = None
    refundable: bool
    cancellation_summary: str
    cancellation: CancellationPolicy
    board: str = "room_only"
    payment_type: str | None = None
    availability_status: AvailabilityStatus
    rooms_remaining: int | None = Field(None, ge=0)
    quote_expires_at: datetime | None = None
    hold_expires_at: datetime | None = None
    source: str
    source_mode: SourceMode
    is_live: bool
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def live_flag_matches_mode(self) -> "RoomRate":
        if self.is_live != (self.source_mode == SourceMode.LIVE):
            raise ValueError("is_live must match source_mode")
        if self.hold_expires_at is not None and not self.provider_metadata.get("hold_reference"):
            raise ValueError("hold expiry requires a supplier hold reference")
        return self


class RoomType(ApiModel):
    id: str
    name: str
    description: str = ""
    occupancy: Occupancy
    beds: list[Bed] = Field(default_factory=list)
    board: str = "varies_by_rate"
    photos: list[str] = Field(default_factory=list)
    rate_plans: list[RoomRate]


class HotelInventory(ApiModel):
    hotel_id: str
    recommendation_id: str
    hotel_name: str
    source: str
    source_mode: SourceMode
    is_live: bool
    checked_at: datetime
    check_in_date: date
    check_out_date: date
    rooms: list[RoomType]
    availability_status: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_inventory(self) -> "HotelInventory":
        if self.check_out_date <= self.check_in_date:
            raise ValueError("check-out must be after check-in")
        if self.is_live != (self.source_mode == SourceMode.LIVE):
            raise ValueError("is_live must match source_mode")
        if self.availability_status == AvailabilityStatus.AVAILABLE and not self.rooms:
            raise ValueError("available hotel inventory must contain rooms")
        return self


class FlightSearchInput(ApiModel):
    origin_airport: str = Field(..., min_length=3, max_length=3)
    destination_airport: str = Field(..., min_length=3, max_length=3)
    departure_date: date | None = None
    return_date: date | None = None
    cabin_class: Literal["economy", "premium_economy", "business", "first"] = "economy"
    adults: int | None = Field(None, ge=1, le=9)

    @field_validator("origin_airport", "destination_airport")
    @classmethod
    def iata_code(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("airport must be a three-letter IATA code")
        return value

    @model_validator(mode="after")
    def different_airports(self) -> "FlightSearchInput":
        if self.origin_airport == self.destination_airport:
            raise ValueError("origin and destination airports must differ")
        if self.return_date and self.departure_date and self.return_date < self.departure_date:
            raise ValueError("return_date must not precede departure_date")
        return self


class FlightSegment(ApiModel):
    id: str
    origin_airport: str
    destination_airport: str
    departing_at: datetime
    arriving_at: datetime
    operating_carrier: str
    marketing_carrier: str | None = None
    flight_number: str | None = None
    duration: str | None = None


class FlightOffer(ApiModel):
    id: str
    carrier: str
    flight_number: str | None = None
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int = Field(..., ge=0)
    stops: int = Field(..., ge=0)
    journey_type: Literal["one_way", "round_trip"] = "one_way"
    return_carrier: str | None = None
    return_flight_number: str | None = None
    return_origin: str | None = None
    return_destination: str | None = None
    return_depart_at: datetime | None = None
    return_arrive_at: datetime | None = None
    return_duration_minutes: int | None = Field(None, ge=0)
    return_stops: int | None = Field(None, ge=0)
    total: Money
    quote_expires_at: datetime | None = None
    hold_expires_at: datetime | None = None
    availability_status: AvailabilityStatus
    source: str
    source_mode: SourceMode
    is_live: bool
    source_metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @model_validator(mode="after")
    def flight_source_truth(self) -> "FlightOffer":
        if self.is_live != (self.source_mode == SourceMode.LIVE):
            raise ValueError("is_live must match source_mode")
        if self.hold_expires_at is not None and not self.source_metadata.get("hold_reference"):
            raise ValueError("hold expiry requires a supplier hold reference")
        return self


class FlightInventory(ApiModel):
    recommendation_id: str
    source: str
    source_mode: SourceMode
    is_live: bool
    checked_at: datetime
    offers: list[FlightOffer]


class ProviderQuote(ApiModel):
    provider_reference: str
    total: Money
    available: bool
    checked_at: datetime
    quote_expires_at: datetime | None = None
    hold_expires_at: datetime | None = None
    source: str
    source_mode: SourceMode
    is_live: bool
    raw_status: str = "quoted"


class AddCartItemInput(ApiModel):
    recommendation_id: str = Field(..., min_length=1, max_length=160)
    rate_plan_id: str | None = Field(None, min_length=1, max_length=200)
    kind: CartItemKind = CartItemKind.HOTEL

    @model_validator(mode="after")
    def hotels_and_flights_need_provider_ids(self) -> "AddCartItemInput":
        if self.kind in {CartItemKind.HOTEL, CartItemKind.FLIGHT} and not self.rate_plan_id:
            raise ValueError("ratePlanId is required for hotel and flight items")
        return self


class CartItem(ApiModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: CartItemKind
    recommendation_id: str
    rate_plan_id: str | None = None
    provider_quote_id: str | None = None
    title: str
    subtitle: str = ""
    status: CartItemState
    total: Money | None = None
    original_total: Money | None = Field(None, exclude=True)
    quote_expires_at: datetime | None = None
    hold_expires_at: datetime | None = None
    source: str
    source_mode: SourceMode
    is_live: bool
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = "Saved price; availability will be checked again before booking."
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class Cart(ApiModel):
    trip_id: str
    version: int = Field(1, ge=1)
    state: CartState = CartState.OPEN
    items: list[CartItem] = Field(default_factory=list)
    saved_expires_at: datetime | None = None
    earliest_hold_expires_at: datetime | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reservation_notice: str = (
        "Items are saved, not reserved. Only an item marked held has supplier-confirmed inventory."
    )


class TransportLeg(ApiModel):
    id: str
    direction: Literal["outbound", "return"]
    order: int = Field(..., ge=1, le=3)
    kind: Literal["ride", "flight"]
    origin: str
    destination: str
    depends_on: list[str] = Field(default_factory=list)
    booking_status: Literal["needs_details", "ready_to_search", "quoted", "booked"]


class TransportPlan(ApiModel):
    outbound: list[TransportLeg]
    return_: list[TransportLeg] = Field(alias="return")
    daily_mobility_separate: bool = True
