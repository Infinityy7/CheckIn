"""One place for talking to OpenAI so the same code isn't pasted everywhere."""

from __future__ import annotations

import asyncio
import json
import logging

from openai import AsyncOpenAI

from config import (
    LLM_MAX_CONCURRENT_CALLS,
    LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY,
    OPENAI_FALLBACK_MODEL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

# one client for the whole app, and a cap on parallel calls
# so we don't slam into rate limits
_client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SECONDS)
_call_limiter = asyncio.Semaphore(LLM_MAX_CONCURRENT_CALLS)


def is_retryable_error(exc: Exception) -> bool:
    # overloaded / rate-limited / timed out, worth another try
    text = str(exc).lower()
    return (
        "429" in text
        or "rate limit" in text
        or "rate_limit" in text
        or "500" in text
        or "502" in text
        or "503" in text
        or "overloaded" in text
        or "timed out" in text
        or "timeout" in text
    )


def is_fatal_error(exc: Exception) -> bool:
    # bad key / no permission / out of credits, retrying won't help
    text = str(exc).lower()
    return (
        "401" in text
        or "403" in text
        or "invalid_api_key" in text
        or "incorrect api key" in text
        or "insufficient_quota" in text
    )


async def generate_text(
    prompt: str,
    system_instruction: str | None = None,
    use_search: bool = False,
    max_output_tokens: int = 4096,
    temperature: float = 0.3,
    cheap: bool = False,
) -> str:
    """Ask OpenAI for text. Tries the main model, falls back to the cheaper one.

    use_search=True turns on the built-in web_search tool (Responses API).
    cheap=True skips straight to the cheap model (profile chat, sketch updates).
    """
    tools = None
    if use_search:
        tools = [{"type": "web_search"}]

    if cheap:
        models = [OPENAI_FALLBACK_MODEL]
    else:
        models = [OPENAI_MODEL, OPENAI_FALLBACK_MODEL]

    last_error: Exception | None = None

    for model in models:
        try:
            async with _call_limiter:
                kwargs = {
                    "model": model,
                    "input": prompt,
                    "max_output_tokens": max_output_tokens,
                    "temperature": temperature,
                }
                if system_instruction:
                    kwargs["instructions"] = system_instruction
                if tools:
                    kwargs["tools"] = tools
                response = await _client.responses.create(**kwargs)
            text = response.output_text
            if not text:
                raise ValueError("OpenAI returned no text content")
            return text
        except Exception as exc:
            if is_fatal_error(exc):
                raise
            if is_retryable_error(exc):
                last_error = exc
                logger.warning("%s failed (%s), trying next model...", model, exc)
                await asyncio.sleep(1)
                continue
            raise

    raise RuntimeError(f"All OpenAI models unavailable. Last error: {last_error}")


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
