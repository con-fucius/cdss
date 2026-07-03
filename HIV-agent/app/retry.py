"""Retry and timeout helpers for transient infrastructure failures."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class RetryExhaustedError(RuntimeError):
    """Raised when async_retry exhausts all attempts."""


async def async_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.2,
    max_delay: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Run an async operation with exponential backoff."""
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await operation()
        except retry_on as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            await asyncio.sleep(min(max_delay, initial_delay * (2 ** attempt)))
    raise RetryExhaustedError(str(last_error)) from last_error


async def with_timeout(coro: Awaitable[T], timeout_seconds: float) -> T:
    """Wrap an awaitable in asyncio.timeout for consistent timeout behavior."""
    async with asyncio.timeout(timeout_seconds):
        return await coro
