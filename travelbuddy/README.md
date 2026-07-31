# TravelBuddy

TravelBuddy is an AI travel command center. Four specialist agents research transportation, accommodation, activities, and food in parallel; a profile-aware ranker returns the top three choices per category; the selected options become a day-by-day itinerary.

The frontend is React 19 + TypeScript + Vite and is served by the existing FastAPI application. The original backend pipeline remains intact.

## Setup

Requirements: Python 3.11+, Node.js 20+, PostgreSQL 17 (or Docker), and an OpenAI API key.

```bash
cd travelbuddy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d db
alembic upgrade head
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

If PostgreSQL already runs locally, set `DATABASE_URL` to that database instead of starting Docker. To import the old SQLite accounts and profiles once:

```bash
.venv/bin/python scripts/migrate_sqlite_to_postgres.py --sqlite-path data/travelbuddy.db
```

## User flow

1. Register or sign in.
2. Complete the nine-question, mascot-led onboarding conversation.
3. Review the generated `character.md` sketch and editable preference controls.
4. Enter destination, dates, budget, travelers, and trip interests.
5. Watch the four research agents finish independently.
6. Compare the top three profile-ranked choices in each category.
7. Select preferred options and generate the itinerary.
8. After the trip, submit one 1–5 check-in so the saved choices and rating gently tune the profile weights.
9. Edit or retake the character profile at any time.

PostgreSQL stores two separate profile artifacts: natural-language `character.md` guides research, while structured weights drive deterministic ranking. The character sketch is generated once and is not regenerated on ordinary visits. Questionnaire drafts, sessions, trips, ratings, and an idempotent preference-event ledger are durable across restarts.

## Character profile API

All profile and trip endpoints require the Bearer token returned by the auth endpoints.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/profile/intake` | Read or resume the nine-question draft |
| `PUT` | `/api/profile/intake/answers/{questionId}` | Validate and save one answer |
| `POST` | `/api/profile/intake/complete` | Generate `character.md` and compile weights |
| `DELETE` | `/api/profile/intake` | Clear the draft/profile and retake onboarding |
| `GET` | `/api/profile/character` | Read the stable character-profile contract |
| `PUT` | `/api/profile/character` | Edit the summary and structured traits |
| `POST` | `/api/profile/character/feedback` | Persist a recommendation like/dislike signal |
| `POST` | `/api/profile/character/reset` | Delete the profile and retake onboarding |

The legacy `GET /api/profile` and `/api/profile/chat` contracts remain available for compatibility.

## Trip API flow

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/trip/preferences` | Create a trip from validated preferences |
| `POST` | `/api/trip/{id}/research` | Stream context and agent progress with SSE |
| `POST` | `/api/trip/{id}/select` | Validate and save recommendation IDs |
| `POST` | `/api/trip/{id}/itinerary` | Stream itinerary generation with SSE |
| `GET` | `/api/trip/{id}` | Read current trip state |
| `GET` | `/api/trips/pending-check-in` | Find the newest completed, unrated trip |
| `PUT` | `/api/trip/{id}/post-trip-feedback` | Save an idempotent 1–5 post-trip rating |

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

- “Show alternatives” reruns the existing research pipeline because the backend does not expose pagination.
- Recommendation candidates depend on agent-provided controlled tags; unverified dietary compatibility is filtered rather than guessed.
- Share copies the current URL and export uses the browser’s print/PDF support; there is no hosted share document or booking provider integration.
- Destination imagery is intentionally represented with map motifs until a licensed image/search proxy is exposed by the backend.

See [docs/PERSONALIZATION.md](docs/PERSONALIZATION.md) for the questionnaire, ranker, learning formula, database layout, and API contracts.

## Reliability baseline

- Failed JSON responses use one stable error shape with a safe message, code, retry flag, and request ID.
- `X-Request-ID` is returned on every API response and appears in server logs for support tracing.
- Research categories fail independently; completed results stay visible and failed categories can be retried.
- Provider details stay in internal logs rather than leaking into the UI.
- Direct Python and frontend dependencies are locked to the tested versions in `requirements.txt`, `requirements-dev.txt`, and `frontend/package-lock.json`.

See [docs/RELIABILITY.md](docs/RELIABILITY.md) for the contract, retry rules, and intentionally deferred architecture work.

See [docs/FRONTEND_ARCHITECTURE.md](docs/FRONTEND_ARCHITECTURE.md) for component and integration details.
