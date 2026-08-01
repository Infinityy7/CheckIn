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

- JSON requests time out after 20 seconds and surface a safe retry message.
- OpenAI SDK retries are disabled. One shared gateway owns a strict two-model call budget, short jittered backoff, queue timeout, and per-call deadline, so nested retries cannot multiply invisibly.
- Rate limits, provider timeouts, and temporary provider failures may move to the configured fallback model.
- Authentication, permissions, quota exhaustion, malformed inputs, and non-temporary errors are not retried automatically.
- Each specialist has a wall-clock deadline. One timed-out category cannot cancel the other three, and unfinished tasks are cancelled if the stream disconnects.
- Repeated model failures open a process-local circuit. After a cooldown, exactly one request probes the model before normal traffic resumes.
- Research has its own concurrency bulkhead below the global limit, reserving capacity for onboarding and itinerary requests.
- The shared context brief is deterministic by default, removing one serial model call. An optional prose enhancement has a short timeout and always falls back safely.
- Long research streams emit heartbeats so proxies and browsers do not treat quiet web searches as dead connections.
- Recommendation and post-trip feedback are shown as saved only after an idempotent database transaction confirms them.
- Research runs use expiring ownership leases, so a second worker cannot overwrite a live run and a crashed worker cannot strand the trip forever.

Authenticated `GET /api/health/agents` exposes sanitized call counts, latency, token totals, queue timeouts, and primary/fallback circuit state. It never contains model names, API keys, prompts, profiles, raw model output, or provider error bodies.

These controls guarantee bounded work and graceful degradation, not guaranteed AI output. Hard constraints are never relaxed to manufacture recommendations. If there is no valid cached category and every configured model fails, the UI reports that category honestly and lets the user retry.

## Deliberately deferred

The following remain part of the planned backend phase and are not hidden by this baseline:

- There is no durable background job queue or dead-letter queue.
- Circuit and concurrency state are process-local rather than shared through Redis.
- Both configured models currently use the same OpenAI account, so model failover does not protect against a full provider, billing, or credential outage.
- No independent search provider or local-model research fallback is enabled yet. Local models require a real retrieval tool; they must never invent live prices or availability.
- There is no operational alerting or distributed tracing service.
- There is no supplier booking, payment, refund, or compensation workflow.

Before real booking, retry and reconciliation rules must be designed separately for each supplier operation.
