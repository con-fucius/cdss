"""Lightweight observability and rate limiting primitives."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_metrics: dict[str, Any] = {
    "cdss_requests_total": 0,
    "cdss_request_duration_seconds_sum": 0.0,
    "cdss_rate_limit_hits_total": 0,
}
_buckets: dict[str, deque[float]] = defaultdict(deque)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            return await call_next(request)
        finally:
            elapsed = time.perf_counter() - started
            _metrics["cdss_requests_total"] += 1
            _metrics["cdss_request_duration_seconds_sum"] += elapsed


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        limit = _limit_for_path(request.url.path)
        if limit <= 0:
            return await call_next(request)

        key = _rate_key(request)
        now = time.monotonic()
        bucket = _buckets[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= limit:
            _metrics["cdss_rate_limit_hits_total"] += 1
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        bucket.append(now)
        return await call_next(request)


def _limit_for_path(path: str) -> int:
    if path == "/chat/stream":
        return int(os.getenv("CDSS_CHAT_RATE_LIMIT_PER_MIN", "60"))
    if path == "/health":
        return int(os.getenv("CDSS_HEALTH_RATE_LIMIT_PER_MIN", "600"))
    return 0


def _rate_key(request: Request) -> str:
    if request.url.path == "/chat/stream":
        return request.headers.get("x-session-id") or request.client.host
    return request.client.host


def metrics_text() -> Response:
    lines = []
    total = int(_metrics["cdss_requests_total"])
    lines.append(f"cdss_requests_total {total}")
    lines.append(
        f"cdss_request_duration_seconds_sum {_metrics['cdss_request_duration_seconds_sum']:.6f}"
    )
    hits = int(_metrics["cdss_rate_limit_hits_total"])
    lines.append(f"cdss_rate_limit_hits_total {hits}")
    # DEPRECATED: old metric name retained temporarily for dashboards/tests
    # that have not yet moved to the clearer hit-count name.
    lines.append(f"cdss_rate_limited_total {hits}")
    return Response("\n".join(lines) + "\n", media_type="text/plain")


def configure_tracing() -> None:
    """Configure OpenTelemetry if optional packages are installed."""
    if os.getenv("CDSS_ENABLE_OTEL", "false").lower() != "true":
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "cdss-api"}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
    except Exception:
        return
