"""Exponential-backoff retry decorator over the four API error classes.

Retry:      RATE_LIMIT (429), TIMEOUT/connection, SERVER (5xx / overloaded)
Never retry: CLIENT (400/401/403/404/422) — retrying a bad key or bad request only burns quota.
Unknown exceptions are re-raised immediately (fail loudly, don't mask bugs).
"""
from __future__ import annotations

import functools
import logging
import random
import time
from enum import Enum
from typing import Callable, Optional

log = logging.getLogger(__name__)


class ErrorClass(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    SERVER = "server"
    CLIENT = "client"


RETRYABLE = {ErrorClass.RATE_LIMIT, ErrorClass.TIMEOUT, ErrorClass.SERVER}


def _status_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def classify_error(exc: BaseException) -> Optional[ErrorClass]:
    """Map SDK-agnostic exceptions (OpenAI / Anthropic / Mistral / httpx) to an error class."""
    name = type(exc).__name__.lower()
    status = _status_code(exc)
    if status == 429 or "ratelimit" in name:
        return ErrorClass.RATE_LIMIT
    if isinstance(exc, TimeoutError) or "timeout" in name or "connection" in name:
        return ErrorClass.TIMEOUT
    if (status is not None and (500 <= status < 600 or status == 529)) or "internalserver" in name or "overloaded" in name:
        return ErrorClass.SERVER
    if (status is not None and 400 <= status < 500) or any(
        k in name for k in ("authentication", "badrequest", "permission", "notfound", "unprocessable")
    ):
        return ErrorClass.CLIENT
    return None


def _retry_after(exc: BaseException) -> Optional[float]:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        val = headers.get("retry-after")
        return float(val) if val is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def with_retries(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Optional[Callable[[int, ErrorClass, float], None]] = None,
):
    """Decorator factory: full-jitter exponential backoff, honours Retry-After."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 — classified below
                    err = classify_error(exc)
                    if err not in RETRYABLE or attempt >= max_attempts:
                        raise
                    delay = min(max_delay, base_delay * 2 ** (attempt - 1))
                    delay = random.uniform(0, delay)  # full jitter avoids thundering herd
                    ra = _retry_after(exc)
                    if ra is not None:
                        delay = max(delay, min(ra, max_delay))
                    log.warning("Retry %d/%d after %s (%.2fs): %s", attempt, max_attempts, err.value, delay, exc)
                    if on_retry:
                        on_retry(attempt, err, delay)
                    sleep(delay)

        return wrapper

    return decorator
