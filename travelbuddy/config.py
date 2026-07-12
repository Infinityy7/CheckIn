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

OPENAI_MODEL: str = "gpt-4.1-mini"
OPENAI_FALLBACK_MODEL: str = "gpt-4o-mini"

MAX_AGENT_RETRIES: int = 2
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

# how long to wait on a single API call before giving up (seconds)
LLM_TIMEOUT_SECONDS: int = 90

# how many API calls may run at the same time
LLM_MAX_CONCURRENT_CALLS: int = 4

# CORS: comma-separated list of allowed origins, "*" for local development
ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# trips older than this get purged from the in-memory store
TRIP_TTL_HOURS: int = 24
