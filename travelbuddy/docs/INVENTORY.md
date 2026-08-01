# Live inventory and saved cart

TravelBuddy can enrich AI recommendations with dated hotel room rates and
flight offers from a supplier. The recommendation pipeline still decides
*what* suits the traveler; the inventory layer checks *what is available now*
and normalizes supplier responses for the frontend.

## What is implemented

- Hotel cards can load room types, occupancy, beds, board basis, nightly and
  total prices, taxes, cancellation terms, availability, and supplier expiry.
- Transport cards can load dated flight offers with carrier, route, times,
  stops, duration, total price, availability, and supplier expiry.
- A trip-owned saved cart can contain an exact room rate, flight offer, ride,
  or restaurant recommendation.
- Hotel rates and flight offers are verified server-side before they enter the
  saved cart. The browser does not provide trusted prices.
- Cart revalidation surfaces expired, unavailable, or changed supplier prices.

There is **no payment or booking endpoint**. Saving an item does not reserve it,
charge the traveler, or guarantee availability.

## Provider modes

Set `INVENTORY_PROVIDER` to one of these values:

| Value | Behavior |
| --- | --- |
| `unavailable` | Default. Makes no supplier requests and fails closed with a safe “not configured” response. |
| `demo` | Returns deterministic sample rooms and flights for UI development and tests. It is always labelled non-live and is not bookable. |
| `duffel` | Uses the Duffel Flights and Stays APIs through the normalized provider adapter. Requires a Duffel access token. |

`DUFFEL_MODE=test` labels Duffel results as test inventory.
`DUFFEL_MODE=live` labels them as live inventory. The setting must match the
environment and permissions of the supplied token; it does not turn a test
token into a live one.

Duffel Stays access must be enabled on the Duffel account before hotel room
searches will work. A Flights-only token is insufficient for hotel inventory.

## Configuration

```dotenv
INVENTORY_PROVIDER=unavailable
DUFFEL_ACCESS_TOKEN=
DUFFEL_MODE=test
DUFFEL_API_BASE_URL=https://api.duffel.com
INVENTORY_HTTP_TIMEOUT_SECONDS=25
CART_TTL_MINUTES=60
```

| Variable | Purpose |
| --- | --- |
| `INVENTORY_PROVIDER` | Selects `unavailable`, `demo`, or `duffel`. |
| `DUFFEL_ACCESS_TOKEN` | Server-side Duffel credential. Never expose it to Vite or the browser. |
| `DUFFEL_MODE` | Marks normalized Duffel results as `test` or `live`. Defaults to `test`. |
| `DUFFEL_API_BASE_URL` | Duffel API origin. Defaults to `https://api.duffel.com`. |
| `INVENTORY_HTTP_TIMEOUT_SECONDS` | Maximum duration of one supplier request. Defaults to 25 seconds. |
| `CART_TTL_MINUTES` | Lifetime of the user's saved shortlist. Defaults to 60 minutes. |

The saved-cart timer is separate from supplier quote and hold timers. A cart
may remain visible after a supplier quote expires; its price must then be
revalidated. If a supplier explicitly returns a hold, the UI shows that exact
hold expiry independently. TravelBuddy does not invent a one-hour reservation.

## Local setup

Install and configure the application as described in the main README, then
choose one inventory mode in `.env`.

For UI development without supplier credentials:

```dotenv
INVENTORY_PROVIDER=demo
```

Start the application:

```bash
.venv/bin/uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`, create a trip, finish research, and open a hotel
or transport recommendation. Demo results are visibly identified as sample,
non-live inventory.

For Duffel test inventory:

```dotenv
INVENTORY_PROVIDER=duffel
DUFFEL_MODE=test
DUFFEL_ACCESS_TOKEN=your_server_side_test_token
```

Restart FastAPI after changing provider settings. Do not commit `.env`.

## API endpoints

Every endpoint requires the Bearer token returned by the authentication flow,
and every trip must belong to the authenticated user.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/trip/{tripId}/hotels/{recommendationId}/rates` | Load dated room types and supplier prices for a hotel recommendation. |
| `GET` | `/api/trip/{tripId}/flights/{recommendationId}/offers` | Load dated flight offers for a transport recommendation. |
| `GET` | `/api/trip/{tripId}/cart` | Read the trip's saved shortlist and independent expiry clocks. |
| `POST` | `/api/trip/{tripId}/cart/items` | Save a server-verified rate/offer or a non-bookable ride/restaurant choice. |
| `DELETE` | `/api/trip/{tripId}/cart/items/{itemId}` | Remove one saved item. |
| `POST` | `/api/trip/{tripId}/cart/revalidate` | Recheck supplier items and report price or availability changes. |

The API returns normalized camel-case contracts. Supplier payloads are treated
as untrusted and are not forwarded directly to the frontend.

## Checkout boundary

This feature stops at inventory comparison and saved-cart revalidation. It does
not collect traveler details, create Duffel orders or Stays bookings, process
payments, issue tickets, or handle refunds. Those operations need a separate
checkout design, provider terms, payment-security review, and failure recovery
before production launch.
