"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# Gemini (research agents + context brief)
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Add it to your .env file or environment "
        "before starting the app."
    )

GEMINI_MODEL: str = "gemini-2.5-flash"
GEMINI_FALLBACK_MODEL: str = "gemini-2.5-flash-lite"

MAX_AGENT_RETRIES: int = 2
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

# How long to wait on a single Gemini call before giving up (seconds)
GEMINI_TIMEOUT_SECONDS: int = 90

# How many Gemini calls may run at the same time (keeps us under rate limits)
GEMINI_MAX_CONCURRENT_CALLS: int = 2

# CORS: comma-separated list of allowed origins, "*" for local development
ALLOWED_ORIGINS: list[str] = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

# Trips older than this are purged from the in-memory store
TRIP_TTL_HOURS: int = 24
