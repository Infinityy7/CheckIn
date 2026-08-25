# Frontend architecture

## Stack

- React 19 and strict TypeScript
- Vite production build emitted to `static/`
- FastAPI serves the compiled SPA and owns every API call
- Lightweight inline SVG/CSS motion for Tavi; no 3D or animation runtime
- Vitest + Testing Library for components, Playwright for browser flows

## Application flow

`App.tsx` is the small orchestration boundary. It restores the Bearer session, loads the profile once, optionally restores the last durable trip, and moves between planner, workspace, and itinerary stages. Domain UI stays in dedicated components:

- `Mascot.tsx`: original Tavi SVG with eight state classes
- `Onboarding.tsx`: deterministic nine-question conversation, saved progress, profile reveal
- `TripForm.tsx`: validated trip inputs matching `TripPreferences`
- `Workspace.tsx`: agent activity, category tabs, score/rank cards, selection docket
- `ProfileDrawer.tsx`: character prose, structured weights, hard boundaries, retake flow
- `PostTripCheckIn.tsx`: accessible, submit-once 1–5 learning signal
- `ItineraryView.tsx`: day timeline, reorder controls, route notes, rating, print layout
- `UI.tsx`: shared buttons, drawers, chat bubbles, loading/empty/error states

`services/api.ts` is the only frontend networking module. It owns authorization headers, JSON errors, and POST-based SSE parsing. Components never contain raw `fetch` calls.

## Profile integration

PostgreSQL stores a versioned `character_md` sketch and structured JSONB weights. The adapter returns camelCase summary, weights, raw answers, version, and timestamps. The sketch guides search; controlled vibe weights, spend choices, pace, dietary needs, and dealbreakers guide deterministic ranking. Card feedback is resolved by owned trip/recommendation IDs, while selected choices and the post-trip rating are learned together after the trip.

## Design system

The visual system uses warm paper, deep ink, moss, terracotta, brass, and route-blue tokens; Newsreader provides editorial display type and Inter handles interface text. Map contours, route markings, stamps, and restrained spatial depth replace generic dashboard patterns. Motion is CSS-only and disabled through `prefers-reduced-motion`.

The editable Figma foundation file is [CheckIn — Mascot Frontend Redesign](https://www.figma.com/design/aYWmhyvkno6LqsfWCUIZxa). It contains 64 variables, 28 semantic color aliases, 11 typography styles, and 3 depth styles. Additional Figma page generation was blocked by the authenticated Starter plan MCP quota; the final coded screens and checked-in screenshots are the detailed visual reference.

## Accessibility and responsive behavior

- Semantic forms, fields, buttons, dialogs, tabs, timelines, and live regions
- Keyboard-visible focus rings and modal close controls
- Accessible mascot labels; decorative SVG internals are hidden
- Reduced-motion support and print-specific itinerary styling
- Layout checks at 390px, 820px, and 1280px, including a browser assertion preventing horizontal overflow
