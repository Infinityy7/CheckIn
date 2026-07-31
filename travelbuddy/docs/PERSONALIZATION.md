# Personalization architecture

TravelBuddy keeps discovery and ranking separate:

1. `character.md` is concise natural-language context. It is sent into the existing context-brief/search flow.
2. Profile weights are validated JSON stored in PostgreSQL JSONB. Only this structured data is scored or learned.

The questionnaire’s free-text “perfect moment” influences the character sketch only. It is never converted into a score. Dealbreakers and dietary requirements are controlled hard constraints, not weights.

## Nine-question profile

The saved answers cover planning style, top three vibes, splurge/save categories, start time, traveler archetype, usual party, food adventurousness, hard constraints, and a free-text perfect moment. Draft answers are saved after every question and resume after refresh or restart.

Completion deterministically compiles the weights. The LLM may polish the character prose, but it cannot create or modify the structured values. A deterministic sketch is used if that polish fails. Completion retries return the existing profile without another model call.

## Ranking

Each research agent requests eight factual candidates and supplies controlled vibe, constraint, and dietary tags. Hard conflicts are removed first. The remaining candidates receive a 0–1 score:

```text
score = 0.30 × budget_fit
      + taste_weight × profile_affinity
      + rating_weight × damped_review_rating
      + 0.15 × trip_vibe_match
```

`taste_weight` is at most 0.30 and grows with profile confidence. Any unused taste weight moves to rating quality. Review ratings use a Bayesian-style prior so a 5.0 with very few reviews does not automatically beat a well-supported 4.7. Category budget allowances respect the user’s splurge/save choices.

After sorting, only the top three options in each category are returned.

## Learning loop

Learning happens after a completed trip when the user submits a 1–5 rating:

- Selected recommendation tags receive a small positive signal.
- Rating is centered at 3: 3 is neutral, 4–5 reinforce selected tags, and 1–2 reduce them.
- Options not selected are ignored; they are not treated as dislikes.
- Per-vibe changes are capped and the vector is normalized back to a total of 1.
- A server-derived event key makes retries idempotent, so the same trip cannot teach the profile twice.

Explicit card likes/dislikes are resolved from an owned trip and recommendation ID. The browser never submits trusted tags or weight changes.

## PostgreSQL layout

- `users`: accounts and password hashes
- `sessions`: hashed bearer tokens with expiry
- `profiles`: versioned `character_md` and JSONB weights
- `profile_intakes`: resumable nine-question drafts
- `trips`: durable agent, selection, and itinerary snapshots
- `trip_feedback`: one post-trip rating per user/trip
- `preference_events`: immutable, idempotent learning audit entries

Trip changes use row locks, and research uses an expiring per-run lease so multiple workers cannot overwrite each other or leave a trip permanently stuck after a crash. Profile edits use optimistic version checks.

## Deployment

Run schema changes through Alembic:

```bash
docker compose up -d db
.venv/bin/alembic upgrade head
```

Production must set `DATABASE_URL`, `OPENAI_API_KEY`, restricted `ALLOWED_ORIGINS`, and non-default PostgreSQL credentials. SQLite remains available only as a unit-test/local compatibility path.
