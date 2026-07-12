# TravelBuddy

AI-powered travel planning API. Four specialist agents research destinations using Claude with web search, then a central LLM assembles your selections into a day-by-day itinerary.

## Setup

```bash
cd travelbuddy
pip install -r requirements.txt
cp .env.example .env
# Add your Anthropic API key to .env
```

## Run

```bash
python main.py
# or
uvicorn main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`.

## API Flow

The app works in 4 steps:

1. **Submit preferences** → get a `trip_id`
2. **Research** → 4 agents search the web in parallel, results stream via SSE
3. **Select** → pick your favorites from the 12 recommendations
4. **Generate itinerary** → Claude assembles a day-by-day plan, streamed via SSE

## Endpoints

### `GET /api/health`

Health check.

```bash
curl http://localhost:8000/api/health
```

```json
{"status": "ok"}
```

---

### `POST /api/trip/preferences`

Submit trip preferences and create a new trip.

```bash
curl -X POST http://localhost:8000/api/trip/preferences \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "Tokyo",
    "start_date": "2026-05-01",
    "end_date": "2026-05-06",
    "budget_tier": "moderate",
    "vibes": ["culture", "food", "history"],
    "group_type": "couple",
    "num_travelers": 2
  }'
```

```json
{
  "trip_id": "a1b2c3d4-...",
  "status": "received",
  "preferences": { ... }
}
```

---

### `POST /api/trip/{trip_id}/research`

Run all 4 agents. Results stream via **Server-Sent Events** — each agent's results appear as soon as it finishes.

```bash
curl -N http://localhost:8000/api/trip/{trip_id}/research -X POST
```

SSE event stream:

```
data: {"event": "context_brief_generated", "brief": "A couple on a moderate budget..."}

data: {"event": "agent_started", "agent": "Accommodation Agent"}
data: {"event": "agent_started", "agent": "Activities Agent"}
data: {"event": "agent_started", "agent": "Restaurant Agent"}
data: {"event": "agent_started", "agent": "Transport Agent"}

data: {"event": "agent_completed", "agent": "Restaurant Agent", "results": [...]}
data: {"event": "agent_completed", "agent": "Activities Agent", "results": [...]}
data: {"event": "agent_completed", "agent": "Accommodation Agent", "results": [...]}
data: {"event": "agent_completed", "agent": "Transport Agent", "results": [...]}

data: {"event": "all_complete", "trip_id": "a1b2c3d4-..."}
```

---

### `POST /api/trip/{trip_id}/select`

Save which recommendations the user picked.

```bash
curl -X POST http://localhost:8000/api/trip/{trip_id}/select \
  -H "Content-Type: application/json" \
  -d '{
    "selections": ["uuid-1", "uuid-2", "uuid-3", "uuid-4"]
  }'
```

```json
{"status": "selections_saved", "count": 4}
```

---

### `POST /api/trip/{trip_id}/itinerary`

Generate the day-by-day itinerary from selections. Streamed via SSE.

```bash
curl -N http://localhost:8000/api/trip/{trip_id}/itinerary -X POST
```

SSE event stream:

```
data: {"event": "itinerary_started", "trip_id": "...", "selection_count": 4}

data: {"event": "itinerary_complete", "itinerary": {"trip_title": "...", "days": [...]}}
```

---

### `GET /api/trip/{trip_id}`

Get the full trip state (preferences, research results, selections, itinerary).

```bash
curl http://localhost:8000/api/trip/{trip_id}
```

---

## Full Test Flow

```bash
# 1. Submit preferences
TRIP=$(curl -s -X POST http://localhost:8000/api/trip/preferences \
  -H "Content-Type: application/json" \
  -d '{
    "destination": "Tokyo",
    "start_date": "2026-05-01",
    "end_date": "2026-05-06",
    "budget_tier": "moderate",
    "vibes": ["culture", "food"],
    "group_type": "couple",
    "num_travelers": 2
  }')
TRIP_ID=$(echo $TRIP | python -c "import sys,json; print(json.load(sys.stdin)['trip_id'])")
echo "Trip ID: $TRIP_ID"

# 2. Research (SSE stream)
curl -N -X POST http://localhost:8000/api/trip/$TRIP_ID/research

# 3. Get trip state to see recommendation IDs
curl -s http://localhost:8000/api/trip/$TRIP_ID | python -m json.tool

# 4. Select recommendations (use real IDs from step 3)
curl -X POST http://localhost:8000/api/trip/$TRIP_ID/select \
  -H "Content-Type: application/json" \
  -d '{"selections": ["id-1", "id-2", "id-3"]}'

# 5. Generate itinerary (SSE stream)
curl -N -X POST http://localhost:8000/api/trip/$TRIP_ID/itinerary
```
