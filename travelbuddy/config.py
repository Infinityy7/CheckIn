"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI (research agents + context brief + itinerary)
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY is not set. Add it to your .env file or environment "
        "before starting the app."
    )

OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_FALLBACK_MODEL: str = os.environ.get("OPENAI_FALLBACK_MODEL", "gpt-4o-mini")

MAX_AGENT_RETRIES: int = int(os.environ.get("MAX_AGENT_RETRIES", "2"))
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

# One place owns retries. The OpenAI SDK's automatic retries are disabled so
# these limits cannot multiply invisibly across the client and the agents.
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
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

# Wall-clock limits around higher-level work. These remain bounded even when a
# model hangs or returns malformed output more than once.
AGENT_DEADLINE_SECONDS: float = float(
    os.environ.get("AGENT_DEADLINE_SECONDS", "150")
)
ITINERARY_DEADLINE_SECONDS: float = float(
    os.environ.get("ITINERARY_DEADLINE_SECONDS", "180")
)
CONTEXT_BRIEF_TIMEOUT_SECONDS: float = float(
    os.environ.get("CONTEXT_BRIEF_TIMEOUT_SECONDS", "12")
)
SSE_HEARTBEAT_SECONDS: float = float(
    os.environ.get("SSE_HEARTBEAT_SECONDS", "15")
)

# CORS: comma-separated list of allowed origins, "*" for local development
ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
