"""One place for talking to Gemini so the same code isn't pasted everywhere."""

from __future__ import annotations

import asyncio
import json
import logging

from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    GEMINI_FALLBACK_MODEL,
    GEMINI_MAX_CONCURRENT_CALLS,
    GEMINI_MODEL,
    GEMINI_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

# one client for the whole app, and a cap on parallel calls
# so we don't slam into rate limits
_client = genai.Client(api_key=GEMINI_API_KEY)
_call_limiter = asyncio.Semaphore(GEMINI_MAX_CONCURRENT_CALLS)


def is_retryable_error(exc: Exception) -> bool:
    # overloaded or rate-limited, worth another try
    text = str(exc)
    return (
        "503" in text
        or "UNAVAILABLE" in text
        or "429" in text
        or "RESOURCE_EXHAUSTED" in text
    )


def is_fatal_error(exc: Exception) -> bool:
    # bad key / no permission, retrying won't help
    text = str(exc)
    return (
        "401" in text
        or "403" in text
        or "PERMISSION_DENIED" in text
        or "API key" in text
        or "API_KEY_INVALID" in text
    )


async def generate_text(
    prompt: str,
    system_instruction: str | None = None,
    use_search: bool = False,
    max_output_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """Ask Gemini for text. Tries the main model, falls back to the cheaper one."""
    tools = None
    if use_search:
        tools = [types.Tool(google_search=types.GoogleSearch())]

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=tools,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )

    last_error: Exception | None = None

    for model in [GEMINI_MODEL, GEMINI_FALLBACK_MODEL]:
        try:
            async with _call_limiter:
                response = await asyncio.wait_for(
                    _client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=GEMINI_TIMEOUT_SECONDS,
                )
            if not response.text:
                raise ValueError("Gemini returned no text content")
            return response.text
        except asyncio.TimeoutError:
            last_error = RuntimeError(f"{model} timed out after {GEMINI_TIMEOUT_SECONDS}s")
            logger.warning("%s timed out, trying next model...", model)
            continue
        except Exception as exc:
            if is_fatal_error(exc):
                raise
            if is_retryable_error(exc):
                last_error = exc
                logger.warning("%s overloaded/rate-limited, trying next model...", model)
                await asyncio.sleep(1)
                continue
            raise

    raise RuntimeError(f"All Gemini models unavailable. Last error: {last_error}")


def parse_json_text(raw_text: str):
    """json.loads but tolerant of ```json fences around the response."""
    text = raw_text.strip()
    if text.startswith("```"):
        if "\n" in text:
            text = text.split("\n", 1)[1]
        else:
            text = text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()
    return json.loads(text)
