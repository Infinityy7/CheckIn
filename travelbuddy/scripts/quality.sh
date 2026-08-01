#!/usr/bin/env bash
# One-command quality gate. Runs every requested section even after a failure.

set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(git -C "$PROJECT_ROOT" rev-parse --show-toplevel)"
FRONTEND_ROOT="$PROJECT_ROOT/frontend"
ARTIFACTS_DIR="${QUALITY_ARTIFACTS_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/travelbuddy-quality-artifacts.XXXXXX")}"
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/travelbuddy-quality-runtime.XXXXXX")"
COVERAGE_LINK="$FRONTEND_ROOT/node_modules/@vitest/coverage-v8"
PYTHON="${QUALITY_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
BACKEND_COVERAGE_MIN="${BACKEND_COVERAGE_MIN:-65}"
FRONTEND_COVERAGE_MIN="${FRONTEND_COVERAGE_MIN:-60}"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${QUALITY_PYTHON:-python3}"
fi

mkdir -p "$ARTIFACTS_DIR"
cleanup() {
  if [[ -L "$COVERAGE_LINK" ]]; then rm -f "$COVERAGE_LINK"; fi
  rm -rf "$RUNTIME_DIR"
}
trap cleanup EXIT

declare -a PASSED=()
declare -a FAILED=()

usage() {
  printf 'Usage: %s [--all|--backend|--frontend|--e2e|--security]\n' "$0"
}

SECTIONS=()
if [[ $# -eq 0 || "${1:-}" == "--all" ]]; then
  SECTIONS=(security backend frontend e2e)
else
  for option in "$@"; do
    case "$option" in
      --backend) SECTIONS+=(backend) ;;
      --frontend) SECTIONS+=(frontend) ;;
      --e2e) SECTIONS+=(e2e) ;;
      --security) SECTIONS+=(security) ;;
      -h|--help) usage; exit 0 ;;
      *) usage; exit 2 ;;
    esac
  done
fi

has_section() {
  local target="$1"
  local section
  for section in "${SECTIONS[@]}"; do
    [[ "$section" == "$target" ]] && return 0
  done
  return 1
}

run_check() {
  local name="$1"
  shift
  local log="$ARTIFACTS_DIR/${name}.log"
  printf '\n[%s] running\n' "$name"
  set +e
  "$@" 2>&1 | tee "$log"
  local status=${PIPESTATUS[0]}
  set -e
  if [[ $status -eq 0 ]]; then
    PASSED+=("$name")
    printf '[%s] passed\n' "$name"
  else
    FAILED+=("$name")
    printf '[%s] failed (exit %s) — %s\n' "$name" "$status" "$log"
  fi
  return 0
}

install_backend_coverage() {
  "$PYTHON" -c 'import coverage' >/dev/null 2>&1 && return 0
  printf '\n[setup] installing transient Python coverage tooling (no manifest changes)\n'
  "$PYTHON" -m pip install 'coverage>=7.10,<8'
}

prepare_frontend_coverage() {
  local version
  version="$(cd "$FRONTEND_ROOT" && node -p "require('./node_modules/vitest/package.json').version")"
  if [[ -d "$COVERAGE_LINK" ]]; then return 0; fi
  local isolated="$RUNTIME_DIR/vitest-coverage"
  printf '\n[setup] preparing isolated Vitest coverage provider %s (no manifest or lockfile changes)\n' "$version"
  npm install --prefix "$isolated" --no-save --package-lock=false "@vitest/coverage-v8@$version"
  mkdir -p "$(dirname "$COVERAGE_LINK")"
  ln -s "$isolated/node_modules/@vitest/coverage-v8" "$COVERAGE_LINK"
}

frontend_unit_with_coverage() {
  prepare_frontend_coverage
  (cd "$FRONTEND_ROOT" && npm test -- \
    --reporter=default \
    --reporter=junit \
    --outputFile.junit="$ARTIFACTS_DIR/frontend-junit.xml" \
    --coverage \
    --coverage.reporter=text \
    --coverage.reporter=json-summary \
    --coverage.reporter=html \
    --coverage.reportsDirectory="$ARTIFACTS_DIR/frontend-coverage" \
    --coverage.thresholds.lines="$FRONTEND_COVERAGE_MIN")
}

if has_section security; then
  run_check secret-scan "$PYTHON" "$PROJECT_ROOT/scripts/scan_secrets.py" \
    --root "$REPO_ROOT" --output "$ARTIFACTS_DIR/secret-scan.json"
fi

if has_section backend; then
  if install_backend_coverage; then
    export COVERAGE_FILE="$ARTIFACTS_DIR/.coverage"
    run_check backend-tests env \
      OPENAI_API_KEY=test-key-not-a-secret \
      DATABASE_URL="sqlite+pysqlite:///$RUNTIME_DIR/backend.db" \
      "$PYTHON" -m coverage run \
      --source="$PROJECT_ROOT" \
      --omit="$PROJECT_ROOT/.venv/*,$PROJECT_ROOT/tests/*,$PROJECT_ROOT/migrations/*,$PROJECT_ROOT/scripts/*" \
      -m pytest "$PROJECT_ROOT/tests" -q --junitxml="$ARTIFACTS_DIR/backend-junit.xml"
    run_check backend-coverage "$PYTHON" -m coverage report --show-missing --fail-under="$BACKEND_COVERAGE_MIN"
    run_check backend-coverage-xml "$PYTHON" -m coverage xml -o "$ARTIFACTS_DIR/backend-coverage.xml"
    "$PYTHON" -m coverage html -d "$ARTIFACTS_DIR/backend-coverage-html" >/dev/null 2>&1 || true
  else
    FAILED+=("backend-coverage-setup")
  fi
fi

if has_section frontend; then
  if [[ ! -d "$FRONTEND_ROOT/node_modules" ]]; then
    run_check frontend-install npm --prefix "$FRONTEND_ROOT" ci
  fi
  run_check frontend-audit npm --prefix "$FRONTEND_ROOT" audit --audit-level=high
  run_check frontend-lint npm --prefix "$FRONTEND_ROOT" run lint
  run_check frontend-typecheck npm --prefix "$FRONTEND_ROOT" run typecheck
  run_check frontend-unit frontend_unit_with_coverage
  run_check frontend-build npm --prefix "$FRONTEND_ROOT" run build
fi

if has_section e2e; then
  if [[ ! -d "$FRONTEND_ROOT/node_modules" ]]; then
    run_check frontend-install-for-e2e npm --prefix "$FRONTEND_ROOT" ci
  fi
  if [[ "${QUALITY_SKIP_BROWSER_INSTALL:-0}" != "1" ]]; then
    run_check e2e-browser bash -c 'cd "$1" && npx playwright install chromium' _ "$FRONTEND_ROOT"
  fi
  run_check e2e-build npm --prefix "$FRONTEND_ROOT" run build
  run_check e2e env \
    OPENAI_API_KEY=test-key-not-a-secret \
    DATABASE_URL="sqlite+pysqlite:///$RUNTIME_DIR/e2e.db" \
    TRAVELBUDDY_E2E_ARTIFACTS="$ARTIFACTS_DIR/e2e-screenshots" \
    PLAYWRIGHT_HTML_OUTPUT_DIR="$ARTIFACTS_DIR/playwright-report" \
    PLAYWRIGHT_JUNIT_OUTPUT_NAME="$ARTIFACTS_DIR/e2e-junit.xml" \
    npm --prefix "$FRONTEND_ROOT" run test:e2e -- --reporter=list,junit,html
fi

printf '\nQuality summary\n'
printf '  Passed: %s\n' "${#PASSED[@]}"
for name in "${PASSED[@]}"; do printf '    - %s\n' "$name"; done
printf '  Failed: %s\n' "${#FAILED[@]}"
for name in "${FAILED[@]}"; do printf '    - %s\n' "$name"; done
printf '  Artifacts: %s\n' "$ARTIFACTS_DIR"

{
  printf '# TravelBuddy quality summary\n\n'
  printf -- '- Passed: **%s**\n' "${#PASSED[@]}"
  printf -- '- Failed: **%s**\n\n' "${#FAILED[@]}"
  if [[ ${#PASSED[@]} -gt 0 ]]; then
    printf '## Passed\n\n'
    for name in "${PASSED[@]}"; do printf -- '- `%s`\n' "$name"; done
    printf '\n'
  fi
  if [[ ${#FAILED[@]} -gt 0 ]]; then
    printf '## Needs attention\n\n'
    for name in "${FAILED[@]}"; do printf -- '- `%s` — see `%s.log`\n' "$name" "$name"; done
  fi
} > "$ARTIFACTS_DIR/quality-summary.md"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "$ARTIFACTS_DIR/quality-summary.md" >> "$GITHUB_STEP_SUMMARY"
fi

if [[ ${#FAILED[@]} -gt 0 ]]; then
  exit 1
fi
