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

Successful research categories remain available when another category fails. The workspace marks the failed agent, labels the result set as partial, and offers an explicit retry. Refreshing research still replaces the entire shortlist and clears selections because recommendation IDs change.

## Retry rules

- JSON requests time out after 20 seconds and surface a safe retry message.
- Long-running research streams use the backend's existing OpenAI timeouts and bounded model fallback; the browser does not blindly replay POST requests.
- Rate limits, provider timeouts, and temporary provider failures may move to the configured fallback model.
- Authentication, permissions, quota exhaustion, malformed inputs, and non-temporary errors are not retried automatically.
- Recommendation and post-trip feedback are shown as saved only after an idempotent database transaction confirms them.
- Research runs use expiring ownership leases, so a second worker cannot overwrite a live run and a crashed worker cannot strand the trip forever.

## Deliberately deferred

The following remain part of the planned backend phase and are not hidden by this baseline:

- There is no durable background job queue.
- There is no provider-specific circuit breaker or operational alerting service.
- There is no supplier booking, payment, refund, or compensation workflow.

Before real booking, retry and reconciliation rules must be designed separately for each supplier operation.
