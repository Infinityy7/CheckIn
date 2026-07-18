# TravelBuddy

TravelBuddy is an AI travel command center. Four specialist agents research transportation, accommodation, activities, and food in parallel; a profile-aware ranker returns the top three choices per category; the selected options become a day-by-day itinerary.

The frontend is React 19 + TypeScript + Vite and is served by the existing FastAPI application. The original backend pipeline remains intact.

## Setup

Requirements: Python 3.11+, Node.js 20+, and an OpenAI API key.

```bash
cd travelbuddy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
cd frontend
npm install
npm run build
cd ..
```

Add `OPENAI_API_KEY` to `.env`, then run:

```bash
.venv/bin/uvicorn main:app --reload --port 8000
```

Open `http://127.0.0.1:8000`. API documentation is at `http://127.0.0.1:8000/docs`.

For frontend development with hot reload:

```bash
cd frontend
npm run dev
```

Vite proxies `/api` requests to FastAPI on port 8000.

## User flow

1. Register or sign in.
2. Complete the six-turn, mascot-led onboarding conversation.
3. Review the generated persistent character profile.
4. Enter destination, dates, budget, travelers, and trip interests.
5. Watch the four research agents finish independently.
6. Compare the top three profile-ranked choices in each category.
7. Select preferred options and generate the itinerary.
8. Edit or retake the character profile at any time.

The natural-language profile and structured traits are stored in SQLite. It is generated once, included in research/ranking, learned from after itinerary creation, and is not regenerated on ordinary visits.

## Character profile API

All profile and trip endpoints require the Bearer token returned by the auth endpoints.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/profile/chat` | Continue the conversational intake |
| `GET` | `/api/profile/character` | Read the stable character-profile contract |
| `PUT` | `/api/profile/character` | Edit the summary and structured traits |
| `POST` | `/api/profile/character/feedback` | Persist a recommendation like/dislike signal |
| `POST` | `/api/profile/character/reset` | Delete the profile and retake onboarding |

The legacy `GET /api/profile` contract remains available for compatibility.

## Trip API flow

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/trip/preferences` | Create a trip from validated preferences |
| `POST` | `/api/trip/{id}/research` | Stream context and agent progress with SSE |
| `POST` | `/api/trip/{id}/select` | Validate and save recommendation IDs |
| `POST` | `/api/trip/{id}/itinerary` | Stream itinerary generation with SSE |
| `GET` | `/api/trip/{id}` | Read current trip state |

## Quality checks

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e

cd ..
.venv/bin/python -m pytest -q
```

The Playwright suite launches FastAPI on port 8010, verifies onboarding, returning-user behavior, profile editing UI, selection, itinerary generation, responsive overflow, and captures the review screenshots in `screenshots/`.

## Current backend boundaries

- Trip state remains in memory and expires after 24 hours; profiles and accounts persist in SQLite.
- “Show alternatives” reruns the existing research pipeline because the backend does not expose pagination.
- Recommendation feedback is stored in the taste profile and affects the next research/ranking run.
- Share copies the current URL and export uses the browser’s print/PDF support; there is no hosted share document or booking provider integration.
- Destination imagery is intentionally represented with map motifs until a licensed image/search proxy is exposed by the backend.

## Reliability baseline

- Failed JSON responses use one stable error shape with a safe message, code, retry flag, and request ID.
- `X-Request-ID` is returned on every API response and appears in server logs for support tracing.
- Research categories fail independently; completed results stay visible and failed categories can be retried.
- Provider details stay in internal logs rather than leaking into the UI.
- Direct Python and frontend dependencies are locked to the tested versions in `requirements.txt`, `requirements-dev.txt`, and `frontend/package-lock.json`.

See [docs/RELIABILITY.md](docs/RELIABILITY.md) for the contract, retry rules, and intentionally deferred architecture work.

See [docs/FRONTEND_ARCHITECTURE.md](docs/FRONTEND_ARCHITECTURE.md) for component and integration details.
