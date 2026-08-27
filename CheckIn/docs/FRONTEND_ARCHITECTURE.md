# Frontend architecture

## Stack

- React 19 and strict TypeScript
- Vite production build emitted to `static/`
- FastAPI serves the compiled SPA and owns every API call
- Lightweight inline SVG/CSS motion for Tavi; no 3D or animation runtime
- Vitest + Testing Library for components, Playwright for browser flows
- Semantic-token theming with light and dark palettes: an explicit choice sets `data-theme` on the root and is persisted as `checkin.theme` (applied by an inline pre-paint script in `index.html`); with no stored choice, `prefers-color-scheme` decides

## Application flow

`App.tsx` is the small orchestration boundary. It restores the Bearer session, loads the profile once, optionally restores the last durable trip, and drives the Plan → Research → Itinerary stage stepper in the nav. It also checks `GET /api/health/agents` (on bootstrap, after each research run, and on demand) and shows a degraded/unavailable banner when research is impaired. Domain UI stays in dedicated components:

- `AuthView.tsx`: detailed account form — name, username with a live availability check, email, phone, password — and sign-in by email or username
- `Mascot.tsx`: original Tavi SVG with eight state classes
- `Onboarding.tsx`: deterministic nine-question conversation, saved progress, profile reveal
- `TripForm.tsx`: validated trip inputs plus the travel party with named co-travellers
- `CompanionManager.tsx` / `CompanionIntake.tsx`: username-first linked members resolved via `GET /api/users/lookup`, with a guest fallback profiled through the four-question chat intake (`POST /api/profile/chat` with `cotraveller_name`); trip creation is gated until every linked member has a completed account profile and every guest is profiled, mirroring the backend guards. Both companion kinds flow into group ranking — linked members contribute their own account profiles
- `Workspace.tsx`: streaming agent rail, cached-result badges from `metadata` cache stamps, "why this ranked" score breakdown, group-fit labelling, like/dislike learning, explicit retry-missing vs full-refresh actions
- `HealthIndicator.tsx`: nav status dot with a details popover (gateway mode, cache counters, circuit states)
- `HotelInventory.tsx` / `FlightOffers.tsx` / `TransportJourney.tsx` / `TripCart.tsx`: live rates, offers, and the cart, using the shared `Countdown` for quote/hold and cart TTL expiries and `SourceBadge` for non-live labelling
- `ProfileDrawer.tsx`: character prose, structured weights, hard boundaries, retake flow, and a read-only companions section
- `ItineraryView.tsx` / `PostTripCheckIn.tsx`: day timeline, reorder controls, route notes, print layout, and the submit-once 1–5 rating with learned-adjustment rows shown after it lands
- `UI.tsx`: shared primitives (see design system)

`services/api.ts` is the only frontend networking module. It owns authorization headers, JSON errors, and POST-based SSE parsing. Components never contain raw `fetch` calls.

## Profile integration

PostgreSQL stores a versioned `character_md` sketch and structured JSONB weights. The adapter returns camelCase summary, weights, raw answers, version, and timestamps. The sketch guides search; controlled vibe weights, spend choices, pace, dietary needs, and dealbreakers guide deterministic ranking. Card feedback is resolved by owned trip/recommendation IDs, while selected choices and the post-trip rating are learned together after the trip.

## Design system

Waymark is the shipped design system, an evolution of the editorial-cartographic identity: Newsreader for display type, Inter for interface text, map contours and route markings over generic dashboard patterns. All color comes from semantic tokens in `src/styles/tokens.css`, which defines both themes (light values on `:root`, dark under `prefers-color-scheme` and `[data-theme='dark']`) plus legacy aliases for pre-redesign CSS. Shared primitives live in `UI.tsx` with `styles/base.css`: `Chip`, `CachedBadge`, `SourceBadge`, `Countdown`, `Banner`, `Meter`, `SegmentedControl`, `Stepper`, `Modal`, `ThemeToggle`, and the status-dot classes. Each domain imports its own stylesheet (`planner`, `workspace`, `inventory`, `identity`, `itinerary`, `ops`, `shell`). Status chips follow a fixed language: live is green, demo/test inventory is amber, cached results are dashed slate (never styled like fresh results), danger is red. Motion is CSS-only and disabled through `prefers-reduced-motion`.

An earlier Figma foundation file, [CheckIn — Mascot Frontend Redesign](https://www.figma.com/design/aYWmhyvkno6LqsfWCUIZxa), predates Waymark; the coded screens are the visual reference.

## Parity

[`docs/PARITY.md`](PARITY.md) maps every backend endpoint to the UI surface that exercises it.

## Accessibility and responsive behavior

- Semantic forms, fields, buttons, dialogs, tabs, timelines, and live regions
- Skip link to main content; one shared focus trap (`useFocusTrap`) behind both `Drawer` and `Modal`
- Keyboard-visible focus rings and modal close controls
- Accessible mascot labels; decorative SVG internals are hidden
- Theme-aware contrast: every status and surface color has a token pair in both palettes
- Reduced-motion support and print-specific itinerary styling
- Layout checks at 390px, 820px, and 1280px, including a browser assertion preventing horizontal overflow
