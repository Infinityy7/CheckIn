# Quality pipeline

Run the complete local gate from `travelbuddy/`:

```bash
./scripts/quality.sh
```

The command does not read, rewrite, or export the developer's `.env`. It injects
a dummy OpenAI key and a disposable SQLite database for tests. Browser supplier
responses are controlled fixtures, so the suite never spends API credit or
books real inventory.

## What it checks

| Section | Checks | Failure evidence |
| --- | --- | --- |
| Security | High-confidence secrets in Git-tracked files; tracked `.env` files | Redacted JSON report |
| Backend | Unit, integration, resilience, persistence, API contract, supplier adapters, and a from-zero PostgreSQL migration in CI | Pytest JUnit, migration log, line coverage |
| Frontend | Dependency audit, ESLint, strict TypeScript, component tests with coverage, production build | Logs and HTML/JSON coverage |
| Browser | First-time/returning flows, inventory/cart behavior, console errors, failed requests, mobile/tablet/desktop overflow | Playwright trace, screenshot, HTML and JUnit reports |

Every section runs even if an earlier check fails, producing one summary rather
than a slow fix-one-bug-at-a-time loop. Reports are written to a temporary
directory printed at the end. CI keeps the same reports for 14 days.

Run a smaller section while iterating:

```bash
./scripts/quality.sh --backend
./scripts/quality.sh --frontend
./scripts/quality.sh --e2e
./scripts/quality.sh --security
```

Coverage tooling is pinned in the development dependency manifests. The browser
runner may download Chromium on its first run. Override the ratchet floors only
for diagnosis:

```bash
BACKEND_COVERAGE_MIN=0 FRONTEND_COVERAGE_MIN=0 ./scripts/quality.sh
```

Do not lower the committed defaults to make a change pass. Add tests or improve
the code, then raise the floors as coverage grows.

## Supplier test rule

Provider code must be exercised through an injected fake/fixture. A fixture
must label itself `test` or `demo`; it must never be displayed as live supplier
inventory. Contract tests should cover at least:

- normal availability with exact dates and prices;
- an expired quote and a changed price;
- unavailable inventory;
- provider timeout or malformed response;
- cart revalidation before booking;
- the difference between a saved quote and a supplier-confirmed hold.

Live-provider probes belong in a separately approved staging workflow with
restricted credentials. They are intentionally excluded from pull requests.

## CI behavior

`.github/workflows/quality.yml` runs four jobs in parallel and finishes with one
required `quality-gate` job. Configure branch protection to require
`Required quality gate`. Failed browser runs upload traces and screenshots;
failed unit runs upload logs, JUnit results, and coverage reports.

When a test fails, open the matching artifact first. The logs contain the exact
failed assertion, while provider values and secret candidates remain redacted.
