"""Application configuration loaded from environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# Gemini (research agents + context brief)
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = "gemini-2.5-flash"
GEMINI_FALLBACK_MODEL: str = "gemini-2.5-flash-lite"


MAX_AGENT_RETRIES: int = 2
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
