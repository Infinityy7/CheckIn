"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

def _bool_env(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes"}


# development | test | production. Production refuses the SQLite fallback when
# DATABASE_URL is missing (see db._database_url).
APP_ENV: str = os.environ.get("APP_ENV", "development").strip().lower() or "development"

# Optional LLM gateway (LiteLLM in Anthropic-native passthrough mode).
# The kill switch: LLM_GATEWAY_ENABLED=false points AsyncAnthropic straight at
# api.anthropic.com with the app-side resilience unchanged, giving instant
# rollback if the gateway misbehaves. When enabled, the app authenticates to
# the gateway with a virtual key and the provider key lives only in the
# gateway's environment.
LLM_GATEWAY_ENABLED: bool = _bool_env("LLM_GATEWAY_ENABLED", "false")
LLM_GATEWAY_BASE_URL: str = os.environ.get(
    "LLM_GATEWAY_BASE_URL", "http://localhost:4000/anthropic"
)
LLM_GATEWAY_API_KEY: str = os.environ.get("LLM_GATEWAY_API_KEY", "")
if LLM_GATEWAY_ENABLED and not LLM_GATEWAY_API_KEY:
    raise RuntimeError(
        "LLM_GATEWAY_ENABLED is true but LLM_GATEWAY_API_KEY is not set. "
        "Create a gateway virtual key, or disable the gateway."
    )

# Anthropic (research agents + context brief + itinerary)
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
if not ANTHROPIC_API_KEY and not LLM_GATEWAY_ENABLED:
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. Add it to your .env file or environment "
        "before starting the app."
    )

ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
ANTHROPIC_FALLBACK_MODEL: str = os.environ.get(
    "ANTHROPIC_FALLBACK_MODEL", "claude-sonnet-5"
)
ANTHROPIC_FALLBACK_MODEL_2: str = os.environ.get(
    "ANTHROPIC_FALLBACK_MODEL_2", "claude-haiku-4-5"
)

MAX_AGENT_RETRIES: int = int(os.environ.get("MAX_AGENT_RETRIES", "2"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

# One place owns retries. The Anthropic SDK's automatic retries are disabled so
# these limits cannot multiply invisibly across the client and the agents.
# Thinking is disabled by default for predictable latency and token cost. Keep
# a kill switch so controlled experiments can re-enable it without touching
# every model call site.
LLM_THINKING_ENABLED: bool = _bool_env("LLM_THINKING_ENABLED", "false")
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
LLM_QUEUE_TIMEOUT_SECONDS: float = float(
    os.environ.get("LLM_QUEUE_TIMEOUT_SECONDS", "12")
)
LLM_RETRY_BASE_DELAY_SECONDS: float = float(
    os.environ.get("LLM_RETRY_BASE_DELAY_SECONDS", "0.4")
)
LLM_RETRY_MAX_DELAY_SECONDS: float = float(
    os.environ.get("LLM_RETRY_MAX_DELAY_SECONDS", "4")
)
LLM_MAX_WEB_SEARCH_CALLS: int = int(
    os.environ.get("LLM_MAX_WEB_SEARCH_CALLS", "3")
)

# Server-side tool loops report `stop_reason="pause_turn"` when they hit their
# own iteration limit. Resuming is one logical turn, not a retry, so it gets a
# separate small budget that stays inside the per-call deadline.
LLM_MAX_PAUSE_CONTINUATIONS: int = int(
    os.environ.get("LLM_MAX_PAUSE_CONTINUATIONS", "3")
)
LLM_ENHANCE_CONTEXT_BRIEF: bool = os.environ.get(
    "LLM_ENHANCE_CONTEXT_BRIEF", "false"
).lower() in {"1", "true", "yes"}

# Research is intentionally allowed one fewer slot than the process-wide cap.
# That reserved slot keeps onboarding and itinerary requests responsive while
# all travel specialists are searching.
LLM_MAX_CONCURRENT_CALLS: int = int(
    os.environ.get("LLM_MAX_CONCURRENT_CALLS", "5")
)
LLM_MAX_RESEARCH_CONCURRENT_CALLS: int = int(
    os.environ.get(
        "LLM_MAX_RESEARCH_CONCURRENT_CALLS",
        str(max(1, LLM_MAX_CONCURRENT_CALLS - 1)),
    )
)

# Stop sending traffic to a repeatedly failing model, then allow one probe
# after the cooldown instead of creating a retry storm.
LLM_CIRCUIT_FAILURE_THRESHOLD: int = int(
    os.environ.get("LLM_CIRCUIT_FAILURE_THRESHOLD", "3")
)
LLM_CIRCUIT_COOLDOWN_SECONDS: float = float(
    os.environ.get("LLM_CIRCUIT_COOLDOWN_SECONDS", "30")
)

# Taste-aware research cache: exact on trip facts, fuzzy on the compiled taste
# vector. Raw budget amounts are too brittle for an exact key, so budgets are
# bucketed geometrically by CACHE_BUDGET_BUCKET_PCT percent.
LLM_CACHE_ENABLED: bool = _bool_env("LLM_CACHE_ENABLED", "true")
CACHE_TASTE_MARGIN: float = float(os.environ.get("CACHE_TASTE_MARGIN", "0.90"))
CACHE_TTL_SECONDS: float = float(os.environ.get("CACHE_TTL_SECONDS", "21600"))
CACHE_BUDGET_BUCKET_PCT: float = float(
    os.environ.get("CACHE_BUDGET_BUCKET_PCT", "10")
)

# PII posture: character.md and profiles are untrusted personal data, so full
# prompt/response capture is off by default everywhere. The app itself only
# ever logs a stable prompt hash; this flag governs whether the gateway is
# allowed to capture message bodies (keep gateway config in lockstep).
LLM_LOG_PROMPTS: bool = _bool_env("LLM_LOG_PROMPTS", "false")
LLM_LOG_RETENTION_DAYS: int = int(os.environ.get("LLM_LOG_RETENTION_DAYS", "30"))

# Wall-clock limits around higher-level work. These remain bounded even when a
# model hangs or returns malformed output more than once.
AGENT_DEADLINE_SECONDS: float = float(
    os.environ.get("AGENT_DEADLINE_SECONDS", "300")
)
ITINERARY_DEADLINE_SECONDS: float = float(
    os.environ.get("ITINERARY_DEADLINE_SECONDS", "360")
)
CONTEXT_BRIEF_TIMEOUT_SECONDS: float = float(
    os.environ.get("CONTEXT_BRIEF_TIMEOUT_SECONDS", "45")
)
# Advisory sanity check on new trip requests. Fail-open by design: past the
# deadline (or with the flag off) trips are created unchecked.
FEASIBILITY_CHECK_ENABLED: bool = _bool_env("FEASIBILITY_CHECK_ENABLED", "true")
FEASIBILITY_TIMEOUT_SECONDS: float = float(
    os.environ.get("FEASIBILITY_TIMEOUT_SECONDS", "25")
)
# The supplier flight lookup that feeds the transport agent is best-effort:
# past this deadline the agent plans with labelled estimates instead.
FLIGHT_BRIEFING_TIMEOUT_SECONDS: float = float(
    os.environ.get("FLIGHT_BRIEFING_TIMEOUT_SECONDS", "30")
)
SSE_HEARTBEAT_SECONDS: float = float(
    os.environ.get("SSE_HEARTBEAT_SECONDS", "15")
)

# CORS: comma-separated list of allowed origins, "*" for local development
ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
