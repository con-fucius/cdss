"""
app/retry.py

Retry and timeout helpers. Vendored pattern from the chronic-disease CDSS,
trimmed to exactly what this system needs: wrapping calls to the two live
external services (facility registry, emergency dispatch) so a transient
failure does not silently propagate as a confusing error during a live
emergency call.

Retry policy is intentionally conservative: retry only on errors that are
plausibly transient (connection errors, 429, 5xx). Never retry on 4xx
client errors other than 429 — those will not resolve by retrying and
retrying them only adds latency during an emergency.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, Tuple, Type, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def with_timeout(coro: Awaitable[T], timeout_seconds: float) -> T:
    """Wrap a coroutine with a hard timeout. Raises asyncio.TimeoutError."""
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


async def async_retry(
    func: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay: float = 0.5,
    retry_on: Tuple[Type[BaseException], ...] = (httpx.TransportError,),
    retryable_status_codes: Optional[set] = None,
) -> T:
    """
    Retry an async callable with exponential backoff and jitter.

    func: a zero-arg callable returning an awaitable (so it can be re-invoked
          on each attempt — pass a lambda wrapping the real call).
    retry_on: exception types that are always retried (network-level failures).
    retryable_status_codes: for httpx.HTTPStatusError, only retry if the
          response status code is in this set. Defaults to 429/500/502/503/504.
    """
    statuses = retryable_status_codes or _DEFAULT_RETRYABLE_STATUS
    last_exc: Optional[BaseException] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in statuses or attempt == max_attempts:
                raise
            last_exc = exc
        except retry_on as exc:
            if attempt == max_attempts:
                raise
            last_exc = exc

        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
        logger.warning(
            "Retry attempt %d/%d after error: %s (sleeping %.2fs)",
            attempt,
            max_attempts,
            last_exc,
            delay,
        )
        await asyncio.sleep(delay)

    # Unreachable in practice — loop always returns or raises — but keeps
    # type checkers satisfied.
    assert last_exc is not None
    raise last_exc
