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

## Retry rules

- JSON requests time out after 20 seconds and surface a safe retry message. The two
  endpoints that wait on a model turn (onboarding completion and profile chat) get a
  90-second budget instead, because a thinking turn is legitimately slower than a read.
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

## LLM gateway topology

An optional self-hosted LiteLLM proxy can sit between the app and Anthropic, in Anthropic-native passthrough mode only. When `LLM_GATEWAY_ENABLED=true`, the app's `AsyncAnthropic` client points its `base_url` at the gateway's `/anthropic` prefix and authenticates with a virtual key. Configuration lives in `gateway/litellm.config.yaml`; the container starts with `docker compose --profile gateway up`. The default is `LLM_GATEWAY_ENABLED=false`, which points the SDK directly at `api.anthropic.com` with the existing app-side resilience. Both paths pass the same test suite.

The split of responsibility is deliberate and non-overlapping:

- The gateway owns provider key custody and virtual keys, per-key rate limits, the cross-worker concurrency cap, spend budgets, and request logging. When it is enabled, the app's per-process semaphores are skipped so concurrency limits cannot double.
- The gateway does not do model fallback, retries, or caching for this app. Passthrough forwards requests byte-for-byte, which is what preserves server-side `web_search`, adaptive thinking, `output_config.effort`, and `pause_turn` resumption — but it bypasses LiteLLM's router, so gateway-side fallback and caching cannot be enabled without risking those features. That preservation is unverified without a live gateway, so the app keeps retries, fallback, and caching on its side.

Retry ownership stays with the app: the `llm_client.generate_text` failover loop holds the entire retry budget. The Anthropic SDK runs with `max_retries=0`; the gateway ships with `litellm_settings.num_retries=0`, `router_settings.num_retries=0`, and no fallbacks configured.

Rollback: set `LLM_GATEWAY_ENABLED=false` (change nothing else) and restart the API. The app talks directly to `api.anthropic.com` with its own bulkheads, circuits, and fallback chain. No data migration is needed; the research cache works on either path.

Unverified without a live gateway, in addition to the deferred items below:

- whether the deployed LiteLLM version enforces per-key rpm/tpm/`max_parallel_requests` and budgets on passthrough routes;
- whether passthrough preserves `web_search` and `pause_turn` end-to-end (byte-for-byte forwarding should, which is exactly why gateway fallback and caching stay off);
- generic identical-request response caching on passthrough (unsupported or unverified; the app's taste-aware cache covers the dominant repeat pattern).

## Research cache rules

An app-side cache sits in front of research LLM calls (`agents/base.py` and `llm_cache.py`), stored in the Postgres table `llm_research_cache` (Alembic migration `20260826_0002`) behind a Redis-portable backend interface.

- A lookup requires an exact match on trip facts: destination, origin, dates, currency, vibes as an order-independent set, group type, traveler count, co-traveller count, agent/category, operation, `use_search`, and budget bucketed geometrically by `CACHE_BUDGET_BUCKET_PCT` (default 10%).
- The compiled taste vector — normalized `vibe_weights` over the ten controlled profile vibes plus `pace_score`, with co-traveller tastes blended 0.6/0.4 as in the ranker — matches fuzzily at cosine similarity >= `CACHE_TASTE_MARGIN` (default 0.90).
- Rows are fresh for `CACHE_TTL_SECONDS` (default 6 hours); expired rows are pruned on write.
- Hits are re-validated and re-ranked with the current user's taste, so dealbreakers and dietary vetoes always apply live. If fewer than 3 candidates survive, the lookup is a miss and live research runs.
- Every served hit stamps each recommendation's metadata with `cached=true`, `cache_age_seconds`, and `cache_similarity` so the UI can label it. A cache hit never masquerades as a fresh live quote.
- Refusals, empty or invalid output, partial results, and capacity timeouts are never cached.
- `LLM_CACHE_ENABLED` (default true) turns the cache off. The deliberate full-refresh path — re-running research when every category already succeeded — sets `force_refresh`, which bypasses lookup but still stores the fresh result.
- Cache rows contain no user identifiers. The taste vector is derived preference data shared across users by design.

Cache hit/miss/store counters appear in the authenticated health snapshot.

## Prompt logging and PII

`character.md` and profiles are untrusted personal data, so full prompt/response capture is PII.

- The app never logs prompt or profile text. Success and failure log lines carry a stable 16-hex `prompt_sha` for correlation only.
- `LLM_LOG_PROMPTS` (default false) governs whether the gateway may capture message bodies. `gateway/litellm.config.yaml` ships `turn_off_message_logging: true`; keep the two in lockstep.
- Refusal explanations are never logged or returned (unchanged).
- `LLM_LOG_RETENTION_DAYS` (default 30) documents gateway log retention (`maximum_spend_logs_retention_period: "30d"`).
- Keys and credentials never appear in logs.
- The only endpoint exposing operational LLM state remains authenticated `GET /api/health/agents`, with the redaction guarantees above. The gateway admin UI and logs are protected by the gateway master key and are not proxied by the app.

## Deliberately deferred

The following remain part of the planned backend phase and are not hidden by this baseline:

- There is no durable background job queue or dead-letter queue.
- Circuit and concurrency state are process-local rather than shared through Redis.
- All three configured models currently use the same Anthropic account, so model failover does not protect against a full provider, billing, or credential outage.
- No independent search provider or local-model research fallback is enabled yet. Local models require a real retrieval tool; they must never invent live prices or availability.
- There is no operational alerting or distributed tracing service.
- There is no supplier booking, payment, refund, or compensation workflow.

Before real booking, retry and reconciliation rules must be designed separately for each supplier operation.
