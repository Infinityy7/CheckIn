"""Small, stable error contract shared by JSON responses and SSE streams."""

from __future__ import annotations

import re
import uuid
import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
logger = logging.getLogger(__name__)


def request_id_for(request: Request) -> str:
    """Return the request ID assigned by middleware, creating one as a fallback."""
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    supplied = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
    request.state.request_id = request_id
    return request_id


def problem(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool,
    details: Any | None = None,
) -> dict[str, Any]:
    """Build the public error shape while retaining FastAPI's ``detail`` field."""
    request_id = request_id_for(request)
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id,
        "retryable": retryable,
    }
    if details is not None:
        error["details"] = details
    return {"detail": message, "error": error}


def stream_problem(
    request: Request,
    *,
    event: str,
    code: str,
    message: str,
    retryable: bool = True,
) -> dict[str, Any]:
    """Build a user-safe SSE failure event using the same fields as JSON errors."""
    return {
        "event": event,
        "error": message,
        "code": code,
        "request_id": request_id_for(request),
        "retryable": retryable,
    }


def _http_code(status_code: int) -> tuple[str, bool]:
    if status_code == 400:
        return "BAD_REQUEST", False
    if status_code == 401:
        return "UNAUTHORIZED", False
    if status_code == 403:
        return "FORBIDDEN", False
    if status_code == 404:
        return "NOT_FOUND", False
    if status_code == 409:
        return "CONFLICT", True
    if status_code == 429:
        return "RATE_LIMITED", True
    if status_code >= 500:
        return "SERVICE_UNAVAILABLE", True
    return "REQUEST_FAILED", False


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code, retryable = _http_code(exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    return JSONResponse(
        status_code=exc.status_code,
        content=problem(request, code=code, message=message, retryable=retryable),
        headers={REQUEST_ID_HEADER: request_id_for(request)},
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    reason = str(first.get("msg", "invalid value"))
    message = f"Check {location}: {reason}." if location else f"Check the request: {reason}."
    details = [
        {
            "field": ".".join(str(part) for part in item.get("loc", []) if part != "body"),
            "message": item.get("msg", "invalid value"),
        }
        for item in errors
    ]
    return JSONResponse(
        status_code=422,
        content=problem(
            request,
            code="VALIDATION_ERROR",
            message=message,
            retryable=False,
            details=details,
        ),
        headers={REQUEST_ID_HEADER: request_id_for(request)},
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "[%s] Unhandled API error: %s",
        request_id_for(request),
        exc,
        exc_info=True,
    )
    message = "TravelBuddy hit an unexpected problem. Your saved work is still safe."
    return JSONResponse(
        status_code=500,
        content=problem(
            request,
            code="INTERNAL_ERROR",
            message=message,
            retryable=True,
        ),
        headers={REQUEST_ID_HEADER: request_id_for(request)},
    )
