# Reliability baseline

This document describes the reliability layer used by the current MVP. PostgreSQL now provides shared sessions, profiles, trip snapshots, resumable intake, and an idempotent learning ledger. Job queues, supplier integrations, payments, and distributed booking workflows remain out of scope.

## Public error contract

Every failed JSON API response keeps FastAPI's human-readable `detail` field and adds a stable object:

```json
{
  "detail": "Research is already running for this trip",
  "error": {
    "code": "CONFLICT",
    "message": "Research is already running for this trip",
    "request_id": "7cb6e84066b24e68b1b00f50f3d3aa34",
    "retryable": true
  }
}
```

The API accepts a safe `X-Request-ID` header or generates one. The same value is returned in the response header, included in error bodies, and written into request logs. The frontend shows the first eight characters as a support reference when available.

Unexpected server and AI-provider details are logged internally but never returned to the browser. Validation failures use the same contract and include a small `details` list of invalid fields.

## Streaming failures

Research and itinerary generation use server-sent events after the HTTP response has started, so later failures cannot become normal JSON error responses. Their failure events therefore include the same `code`, `request_id`, and `retryable` fields.

Successful research categories remain available when another category fails. The workspace marks the failed agent, labels the result set as partial, and offers an explicit retry. A partial retry fills only missing categories. A deliberate full refresh replaces every category and clears selections because recommendation IDs may change.

The backend keeps the last valid result for each category while a replacement is running. A retry after a partial run starts only the missing categories and reuses the saved context brief. A full refresh still invalidates selections, but an agent outage no longer deletes the last-known-good cards. Results are upserted by agent name, so reconnects and retries cannot create duplicate categories.

Itinerary generation holds a ten-minute lease per trip (`itinerary_in_progress`, `itinerary_started_at`, `itinerary_lease_id`). A second build while the lease is live gets HTTP 409 with code `ITINERARY_IN_PROGRESS`, so two tabs cannot double-spend a model call or overwrite each other. Each stored itinerary carries a fingerprint of the inputs it was built from: the sorted selection IDs plus the exact hotel rate and flight offer IDs saved in the cart. Saving a different selection set, or adding or removing an exact rate or offer, clears the itinerary; a rebuild with an unchanged fingerprint replays the stored plan (the `itinerary_complete` event carries `"replayed": true`) and makes no model call. Passive saved-cart expiry does not delete an itinerary, but the fingerprint no longer matches, so the next build regenerates rather than replaying. The plan itself is validated after parsing: dates must cover the trip exactly, day numbers must be in order, every day needs items, categories are controlled, and every selected recommendation must appear; a rejected plan is retried once with the specific reason before `itinerary_failed` is streamed.

Itinerary builds hold a ten-minute lease that is released before any awaited cleanup, so a browser that disconnects mid-build never locks the trip; a build whose selections or saved cart choices changed while the model was working is discarded with `ITINERARY_INPUTS_CHANGED` instead of being stored against the old inputs.

## Retry rules

- JSON requests time out after 20 seconds and surface a safe retry message. Three
  endpoints wait on a model turn and get a longer budget: onboarding completion and
  profile chat (90 seconds, including provider and gateway headroom)
  and trip creation (`TRIP_CREATE_TIMEOUT_MS`, 45 seconds), whose advisory feasibility
  check is itself bounded by `FEASIBILITY_TIMEOUT_SECONDS` (25 seconds, fail-open to
  `unchecked`). The client deadline is deliberately longer than the whole server-side
  operation so the browser cannot give up on a request the server is still about to complete.
- Trip creation is idempotent per `(user, Idempotency-Key)`. The client reuses one key for the
  first submit, an error retry of the same values, and "Research anyway"; the server replays the
  stored trip (`"replayed": true`) without another feasibility call. A held trip (verdict
  `unrealistic`, not acknowledged) creates no row, so revising the request cannot orphan a trip.
- Anthropic SDK retries are disabled (`max_retries=0`). The app's failover loop in `llm_client.generate_text` is the single retry owner: it holds a strict call budget across the chain primary, then fallback, then fallback_2 (defaults `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`), plus short jittered backoff, queue timeout, and per-call deadline, so nested retries cannot multiply invisibly. When the LLM gateway is enabled, its retries and fallbacks are also zero, keeping this loop the only owner.
- A model is asked for at most one logical turn. Resuming a paused server-side web-search loop stays inside that turn and inside the same per-call deadline; a turn that never finishes inside its continuation budget is treated as a retryable failure of that model.
- A safety refusal is a verdict about one model, not the account, so it fails over to the configured fallback. The refusal explanation is never logged or returned.
- Rate limits, provider timeouts, and temporary provider failures may move to the configured fallback model.
- Authentication, permissions, quota exhaustion, malformed inputs, and non-temporary errors are not retried automatically.
- Each specialist has a wall-clock deadline. One timed-out category cannot cancel the other three, and unfinished tasks are cancelled if the stream disconnects.
- Repeated model failures open a process-local circuit. After a cooldown, exactly one request probes the model before normal traffic resumes.
- Research has its own concurrency bulkhead below the global limit, reserving capacity for onboarding and itinerary requests.
- The shared context brief is deterministic by default, removing one serial model call. An optional prose enhancement has a short timeout and always falls back safely.
- Long research streams emit heartbeats so proxies and browsers do not treat quiet web searches as dead connections.
- Recommendation and post-trip feedback are shown as saved only after an idempotent database transaction confirms them.
- Research runs use expiring ownership leases, so a second worker cannot overwrite a live run and a crashed worker cannot strand the trip forever.

Authenticated `GET /api/health/agents` exposes sanitized call counts, latency, token totals, queue timeouts, primary/fallback circuit state, a gateway section (`enabled`, `mode`), and research cache hit/miss/store counters. It never contains model names, API keys, prompts, profiles, raw model output, or provider error bodies.

These controls guarantee bounded work and graceful degradation, not guaranteed AI output. Hard constraints are never relaxed to manufacture recommendations. If there is no valid cached category and every configured model fails, the UI reports that category honestly and lets the user retry.

## Trip creation

`POST /api/trip/preferences` takes `TripPreferences` plus `feasibility_acknowledged` (default false) and an optional `Idempotency-Key` header (8-128 characters of `[A-Za-z0-9._:-]`, otherwise 400). New trips must start today (UTC) or later; stored trips keep loading after their dates pass. The flow is:

1. Validate companions (guest names and accepted linked accounts).
2. If a trip already exists for this `(user, key)`, return it with `"replayed": true` and its stored feasibility report. No model call runs.
3. Unless acknowledged, run the advisory feasibility check inside `FEASIBILITY_TIMEOUT_SECONDS` (`FEASIBILITY_CHECK_ENABLED=false` skips it). A timeout or model error degrades to `unchecked`; it never blocks creation.
4. Verdict `unrealistic` without acknowledgement returns HTTP 200 `{"trip_id": null, "status": "held", "replayed": false, "preferences", "feasibility"}` and creates **no** trip row. The client shows the suggestion beside the submitted values; "Research anyway" resubmits the same key with `feasibility_acknowledged: true`.
5. Otherwise exactly one trip is created (`"status": "received"`). `trips.idempotency_key` carries a unique index on `(user_id, idempotency_key)` (migration `20260901_0004`), so two concurrent requests with the same key resolve to the same trip: the loser's insert hits the index and returns the winner's row.

Without the header the endpoint behaves as before: every request creates a trip. The frontend mints a new key whenever the submitted values change and clears it after a successful creation.

`APP_ENV=production` refuses the SQLite fallback when `DATABASE_URL` is empty, and `init_db()` refuses to start a PostgreSQL worker whose `alembic_version` differs from the code's migration head, so a partially deployed schema fails fast instead of at the first write.

## Planning scope

`TripPreferences.scope` says which parts of the trip CheckIn plans: a non-empty subset of `transport`, `hotel`, `activity`, `restaurant`, deduplicated and stored in that canonical order. It defaults to all four, so every older client, fixture, and stored trip behaves exactly as before; an unknown value or an empty list is a 422 with a plain message.

- Research runs only the specialists for the chosen categories (`orchestrator.agents_for_scope`, backed by `AGENT_CATEGORIES`, agent name to category). The failed/completed sets, the partial-retry target, and the full-refresh detection are all computed against the scoped agents, so a stored result or error from an out-of-scope category never counts and cannot turn a first run into a "partial retry". `research_started.agents` and `available_categories` likewise cover only the scoped agents.
- For a partial scope the deterministic context brief ends with "Planning scope: CheckIn is planning only ...; the traveler arranges the rest separately." That sentence enters the research cache's prompt fingerprint, so a flights-only trip never reads a cached row written for a full plan of the same facts.
- The feasibility check skips the deterministic lodging-and-food floor when neither `hotel` nor `restaurant` is in scope (a small transport-only budget is not "unrealistic" on those grounds), and its prompt gains a "Planning scope" section so the model judges only what is being planned and budgeted.
- The itinerary prompt gains the same section for partial scopes: the model must not invent hotels, restaurants, transport, or activities outside the scope and writes `free_time` placeholders such as "Traveler arranges lodging separately" instead. Itinerary validation is unchanged.

## LLM gateway topology

An optional self-hosted LiteLLM proxy can sit between the app and Anthropic, in Anthropic-native passthrough mode only. When `LLM_GATEWAY_ENABLED=true`, the app's `AsyncAnthropic` client points its `base_url` at the gateway's `/anthropic` prefix and authenticates with a virtual key. Configuration lives in `gateway/litellm.config.yaml`; the container starts with `docker compose --profile gateway up`. The default is `LLM_GATEWAY_ENABLED=false`, which points the SDK directly at `api.anthropic.com` with the existing app-side resilience. Both paths pass the same test suite.

The split of responsibility is deliberate and non-overlapping:

- The gateway owns provider key custody and virtual keys, per-key rate limits, the cross-worker concurrency cap, spend budgets, and request logging. When it is enabled, the app's per-process semaphores are skipped so concurrency limits cannot double.
- The gateway does not do model fallback, retries, or caching for this app. Passthrough forwards requests byte-for-byte, preserving server-side `web_search`, the selected thinking mode, `output_config.effort`, and `pause_turn` resumption. It bypasses LiteLLM's router, so the app keeps retries, fallback, and its taste-aware research cache on its side.

Retry ownership stays with the app: the `llm_client.generate_text` failover loop holds the entire retry budget. The Anthropic SDK runs with `max_retries=0`; the gateway ships with `litellm_settings.num_retries=0`, `router_settings.num_retries=0`, and no fallbacks configured.

Rollback: set `LLM_GATEWAY_ENABLED=false` (change nothing else) and restart the API. The app talks directly to `api.anthropic.com` with its own bulkheads, circuits, and fallback chain. No data migration is needed; the research cache works on either path.

Unverified without a live gateway, in addition to the deferred items below:

- whether the deployed LiteLLM version enforces per-key rpm/tpm/`max_parallel_requests` and budgets on passthrough routes;
- whether passthrough preserves `web_search` and `pause_turn` end-to-end (byte-for-byte forwarding should, which is exactly why gateway fallback and caching stay off);
- generic identical-request response caching on passthrough (unsupported or unverified; the app's taste-aware cache covers the dominant repeat pattern).

## Research cache rules

An app-side cache sits in front of research LLM calls (`agents/base.py` and `llm_cache.py`), stored in the Postgres table `llm_research_cache` (Alembic migration `20260826_0002`) behind a Redis-portable backend interface.

- A lookup requires an exact match on trip facts: destination, origin, dates, currency, vibes as an order-independent set, group type, traveler count, co-traveller count, agent/category, operation, `use_search`, and budget bucketed geometrically by `CACHE_BUDGET_BUCKET_PCT` (default 10%).
- The exact key also carries `key_version` (currently 2), `prompt_schema_version` (a constant in `llm_cache.py`, bumped whenever a prompt or the recommendation schema changes shape), the inventory provider's `supplier_mode` (`live`, `test`, `demo`, or `unavailable`), and a `prompt_fingerprint`: SHA-256 over the shared context brief plus canonical JSON of the user's and co-travellers' taste dictionaries. Only the hash is stored, never the prose.
- Cached payloads include reasoning written against one profile, so the fingerprint is what keeps that reasoning inside a profile boundary: two accounts with identical trip facts but different profile prose or taste data produce different keys and cannot read each other's rows. A profile version change alters the brief or taste dictionary and therefore invalidates the row for the same user as well.
- Rows written under an older `key_version` or `prompt_schema_version`, or under a different supplier mode, can never match again. Switching the inventory provider (for example enabling supplier flight briefings) invalidates every transport row produced without them, so an old web-researched flight payload cannot bypass the supplier context.
- The compiled taste vector — normalized `vibe_weights` over the ten controlled profile vibes plus `pace_score`, with co-traveller tastes blended 0.6/0.4 as in the ranker — is still stored and still gates a hit at cosine similarity >= `CACHE_TASTE_MARGIN` (default 0.90), as a second guard behind the fingerprint.
- Rows are fresh for `CACHE_TTL_SECONDS` (default 6 hours). Every store, and at most one lookup per minute per process, deletes all expired rows regardless of key (`db.research_cache_prune`), so a stale personalized payload does not linger until its own key is written again. The prune count is part of the cache stats.
- Hits are re-validated and re-ranked with the current user's taste, so dealbreakers and dietary vetoes always apply live. If fewer than 3 candidates survive, the lookup is a miss and live research runs.
- Every served hit stamps each recommendation's metadata with `cached=true`, `cache_age_seconds`, and `cache_similarity` so the UI can label it. A cache hit never masquerades as a fresh live quote.
- Refusals, empty or invalid output, partial results, and capacity timeouts are never cached.
- `LLM_CACHE_ENABLED` (default true) turns the cache off. The deliberate full-refresh path — re-running research when every category already succeeded — sets `force_refresh`, which bypasses lookup but still stores the fresh result.
- Cache rows contain no user identifiers. Request facts hold the opaque fingerprint and the derived taste vector only; profile prose is never written to the cache table.
- Every specialist emits only its own category: a candidate whose `category` differs from the agent's is overwritten before validation, so a mislabelled item cannot be cached or served under another category.

Cache hit/miss/store counters appear in the authenticated health snapshot.

## Prompt logging and PII

`character.md` and profiles are untrusted personal data, so full prompt/response capture is PII.

- The app never logs prompt or profile text. Success and failure log lines carry a stable 16-hex `prompt_sha` for correlation only.
- `LLM_LOG_PROMPTS` (default false) governs whether the gateway may capture message bodies. `gateway/litellm.config.yaml` ships `turn_off_message_logging: true`; keep the two in lockstep.
- Refusal explanations are never logged or returned (unchanged).
- `LLM_LOG_RETENTION_DAYS` (default 30) documents gateway log retention (`maximum_spend_logs_retention_period: "30d"`).
- Keys and credentials never appear in logs.
- The only endpoint exposing operational LLM state remains authenticated `GET /api/health/agents`, with the redaction guarantees above. The gateway admin UI and logs are protected by the gateway master key and are not proxied by the app.

## Companion consent and guest intake

Another account's character profile is never read merely because its username is known. `companions.py` is the only module that touches a companion's profile, and it does so only while the organizer→member row in `companion_links` (Alembic migration `20260901_0005`) is `accepted`.

- Invitation states: `pending` (the organizer invited, no answer yet), `accepted` (the member agreed, so their compiled taste joins the organizer's research), `declined` (the member said no or removed the link), and `revoked` (the organizer withdrew it). Re-inviting a `declined` or `revoked` link returns it to `pending`; inviting a `pending` or `accepted` link changes nothing. There is one row per (inviter, invitee) pair and the direction matters: B accepting A's invitation does not let B use A's profile.
- Trip creation rejects a linked username whose link is not `accepted` with HTTP 403, before it checks whether that member's profile is complete. Research re-reads the link on every run, so a decline or revoke after the trip exists removes that profile from the next run.
- The organizer never receives a member's sketch text. `GET /api/users/lookup` and the `/api/companions/links` rows carry only `username`, `name`, and the link status. Because the shared context brief is streamed to and stored for the organizer, a linked member contributes only their compiled taste weights to research; their prose is never placed in the brief. Guest sketches the organizer wrote stay in the brief as before.
- Guest intake turns are durable. Each guest conversation lives in `profile_intakes` (`kind='cotraveller'`), so a server restart or another worker continues the same thread, and `GET /api/profile/chat?cotraveller_name=` restores it after a page reload. Every submitted answer carries a client `turn_key`: resending it replays the stored reply without appending or spending a model call, an empty opener replays the last question, and a repeated final answer replays the completion instead of regenerating the sketch. The legacy self-profile chat stays process-local but dedupes by the same key.

## Deliberately deferred

The following remain part of the planned backend phase and are not hidden by this baseline:

- There is no durable background job queue or dead-letter queue.
- Circuit and concurrency state are process-local rather than shared through Redis.
- All three configured models currently use the same Anthropic account, so model failover does not protect against a full provider, billing, or credential outage.
- No independent search provider or local-model research fallback is enabled yet. Local models require a real retrieval tool; they must never invent live prices or availability.
- There is no operational alerting or distributed tracing service.
- There is no supplier booking, payment, refund, or compensation workflow.

Before real booking, retry and reconciliation rules must be designed separately for each supplier operation.
