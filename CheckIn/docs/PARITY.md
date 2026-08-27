# Backend ↔ frontend parity matrix

Deliverable 1 of the frontend redesign. "Before" is the pre-redesign UI; "After" is where the
redesigned UI surfaces the capability. The backend contract is fixed; every row below must be
reachable in the shipped UI.

| # | Backend capability | Before (reachable?) | After: surface in redesign |
|---|---|---|---|
| 1 | POST /api/auth/register (optional `username`/`name`/`phone` — required in the UI form, auto-derived server-side for legacy clients), /login (`identifier` accepts email or username) | Yes — email + password only | AuthView detailed account form (name, username with availability check, email, phone, password); sign-in by email or username |
| 2 | POST /api/auth/logout | Yes — nav | Nav account menu |
| 3 | GET /api/auth/me (incl. `username`/`name`/`phone`, `cotravellers[]`) | Partial — session bootstrap only; cotravellers ignored | Bootstrap + Travel-party manager reads saved companions |
| 4 | GET /api/profile/intake | Yes — Onboarding | Onboarding (restyled) |
| 5 | PUT /api/profile/intake/answers/{id} | Yes — Onboarding | Onboarding |
| 6 | POST /api/profile/intake/complete | Yes — Onboarding | Onboarding (handles completion_failed retry) |
| 7 | DELETE /api/profile/intake | Yes — retake flow | Profile drawer retake |
| 8 | GET /api/profile/character | Yes — ProfileDrawer | Profile drawer |
| 9 | PUT /api/profile/character | Yes — ProfileDrawer | Profile drawer (optimistic-version conflict UX kept) |
| 10 | POST /api/profile/character/reset | Yes — retake fallback | Profile drawer |
| 11 | POST /api/profile/character/feedback `like` | **No — UI only ever sent `dislike`** | Like + dislike on every recommendation card |
| 12 | POST /api/profile/character/feedback `dislike` | Yes — "Not my thing" | Kept |
| 13 | POST /api/profile/chat (self, legacy) | No component path (SDK method unused) | Intentionally not resurfaced for self (questionnaire is canonical); kept in SDK for rollback deployments |
| 14 | **POST /api/profile/chat with `cotraveller_name`** | **No — orphaned; SDK method didn't even accept the field** | Companion intake chat (4-question flow per companion) launched from Travel-party manager |
| 15 | **GET /api/profile (sketch + cotravellers)** | **No — not in api.ts at all** | Travel-party manager companion list ("profiled" status) |
| 16 | POST /api/trip/preferences (guest `cotravellers` + linked `cotraveller_usernames`: each username must be an existing account with a completed taste profile, self-add rejected, max 8 companions combined) | Partial — worked, but `cotravellers` hardcoded `[]`, so the main.py guard + group ranking never ran; `wellness` vibe missing from the form | Trip form with guests gated on chat-intake profiles and linked members gated on account profiles (mirrors the backend 400s), full 11-vibe set |
| 17 | GET /api/trip/{id} | Yes — restore/hydrate | Kept |
| 18 | POST /api/trip/{id}/research SSE (agent events, heartbeats) | Yes — Workspace stream | Kept, restyled streaming rail |
| 19 | Research **partial retry** (only failed categories) | Yes — "Retry research" after failures | Explicit "Retry missing categories" |
| 20 | Research **full refresh** (all complete → replaces all, clears selections) | Partial — "Show alternatives" triggered it without saying selections would be cleared | Explicit "Full refresh" with consequence warning; only offered when backend would actually full-refresh |
| 21 | **Cached results: `metadata.cached` / `cache_age_seconds` / `cache_similarity`** | **No — never read** | Cached badge on each cached card (age + taste-match %); never styled like a live/fresh result |
| 22 | Ranked top-3 w/ score, reasoning, `score_breakdown` (incl. `matched[]`) | Partial — score ring + reasoning only; types.ts mistyped `score_breakdown` | "Why this ranked" disclosure: budget/taste/rating/vibe meters + matched taste tags; group-fit strip when companions exist |
| 23 | Group-aware ranking (least-misery, per-member vetoes; research blends linked members' own account sketches/tastes alongside guest profiles) | No (feature unreachable, see #16) | Group-fit badge ("balanced across N travellers"); vetoed-candidate explanation note. ⚠️ Flagged: the backend filters conflicted candidates out server-side (ranking.py drops any rec with conflicts), so per-member veto details for dropped items are **not in the response contract**; the UI surfaces what exists (group score, matched tags) and explains the filtering honestly. |
| 24 | POST /api/trip/{id}/select | Yes — docket → build | Kept |
| 25 | POST /api/trip/{id}/itinerary SSE + view | Yes — ItineraryView | Kept, restyled |
| 26 | GET hotels/{recId}/rates | Yes — HotelInventory per hotel card | Kept |
| 27 | GET flights/{recId}/offers | Yes — FlightOffers via TransportJourney | Kept |
| 28 | Demo/test inventory labelling (`sourceMode`, `isLive`) | Yes — source pill | Kept (non-live label preserved verbatim rule) |
| 29 | GET/POST/DELETE cart items | Yes — TripCart in docket | Kept |
| 30 | POST cart/revalidate | Yes — "Recheck all prices" | Kept |
| 31 | Cart TTL (`savedExpiresAt`) + quote/hold expiries | Yes — ExpiryCountdown | Kept; countdown promoted to shared primitive |
| 32 | GET /api/trips/pending-check-in | Yes — planner banner | Kept |
| 33 | PUT /api/trip/{id}/post-trip-feedback (+ weight adjustments) | Yes — PostTripCheckIn | Kept; adjustment rows shown after rating |
| 34 | **GET /api/health** | **No** | Used as liveness for the ops indicator |
| 35 | **GET /api/health/agents (gateway mode, cache stats, circuits)** | **No — not in api.ts** | Nav status dot + degraded/unavailable banner when research is impaired; details popover (status, gateway mode, cache hit counters, circuit states) |
| 36 | Error envelope {code, message, request_id, retryable} | Yes — ApiError + stream problems | Kept; retryable ⇒ retry affordance everywhere |
| 37 | **GET /api/users/lookup** (`username` → `{username, name, intake_complete}`; 404 when unknown; auth required) | **No — new endpoint** | Registration username-availability check + trip-form linked-member lookup |

Identity migration `20260827_0003_user_identity`: adds `username`/`name`/`phone` to `users` with a unique `lower(username)` index, backfilling usernames from the email local part (suffixed on collision).

## Type-contract fixes (extend, don't fork)
- `Recommendation.score_breakdown` was `Record<string, number>`; actually carries `matched: string[]` and `conflicts: string[]` alongside numeric signals — fixed.
- `Recommendation` lacked `vibe_tags`, `constraint_tags`, `dietary_tags`, `dietary_conflicts` — added (optional).
- `StreamEvent` lacked `resumed`, `agents`, `status`, `trip_id`, `available_categories` — added.
- New types: `AgentHealth`, `ProfileOverview`, cache-stamp readers.

## Flagged backend asymmetries (not worked around silently)
1. Per-member veto detail for *dropped* candidates never reaches the client (filtered in ranking.py before serialization) — see row 23.
2. `POST /api/profile/chat` without `cotraveller_name` (self chat) is a legacy path superseded by the questionnaire; kept in the SDK, not in the UI.
3. TripForm previously offered `family-friendly` (valid trip override) but omitted `wellness` (a canonical profile vibe accepted by schemas.ALLOWED_VIBES) — fixed in the redesign.
