"""
app/observability.py

Metrics counters and in-process rate limiting.
Adapted from the chronic-disease CDSS observability.py, trimmed: no
OpenTelemetry toggle for this build (no distributed tracing need yet at
this scale), kept the part that matters most for an emergency-call system:
per-endpoint latency histograms (this is how you know whether
"time from call-received to unit-dispatched" is degrading) and rate
limiting on the dispatch/field write endpoints so a runaway client cannot
starve a real call.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable, Dict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_LATENCY_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

_request_counts: Dict[str, int] = defaultdict(int)
_latency_bucket_counts: Dict[str, Dict[float, int]] = defaultdict(
    lambda: {b: 0 for b in _LATENCY_BUCKETS}
)
_latency_sum: Dict[str, float] = defaultdict(float)
_rate_limit_hits: Dict[str, int] = defaultdict(int)

# endpoint_path -> {client_key -> [timestamps]}
_rate_window: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))


def _record_latency(endpoint: str, seconds: float) -> None:
    _request_counts[endpoint] += 1
    _latency_sum[endpoint] += seconds
    for bucket in _LATENCY_BUCKETS:
        if seconds <= bucket:
            _latency_bucket_counts[endpoint][bucket] += 1


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        endpoint = request.url.path
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        _record_latency(endpoint, elapsed)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-client-key sliding window rate limit, applied selectively.
    Client key is the X-Client-Id header if present (dispatcher/field unit
    identity), else the request's client host as a fallback.
    """

    def __init__(self, app, limited_paths: Dict[str, int]):
        super().__init__(app)
        # limited_paths: {path_prefix: max_requests_per_minute}
        self._limited_paths = limited_paths

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        limit = None
        for prefix, max_per_minute in self._limited_paths.items():
            if path.startswith(prefix):
                limit = max_per_minute
                break

        if limit is not None:
            client_key = request.headers.get("X-Client-Id") or (
                request.client.host if request.client else "unknown"
            )
            now = time.time()
            window = _rate_window[path][client_key]
            window[:] = [t for t in window if now - t < 60.0]
            if len(window) >= limit:
                _rate_limit_hits[path] += 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Please retry shortly."},
                )
            window.append(now)

        return await call_next(request)


def metrics_text() -> str:
    """Prometheus-style plaintext metrics output."""
    lines = []
    for endpoint, count in _request_counts.items():
        safe = endpoint.replace('"', "")
        lines.append(
            f'ambulance_cdss_requests_total{{endpoint="{safe}"}} {count}'
        )
        lines.append(
            f'ambulance_cdss_request_duration_seconds_sum{{endpoint="{safe}"}} '
            f"{_latency_sum[endpoint]:.6f}"
        )
        for bucket, bucket_count in _latency_bucket_counts[endpoint].items():
            lines.append(
                f'ambulance_cdss_request_duration_seconds_bucket{{endpoint="{safe}",le="{bucket}"}} '
                f"{bucket_count}"
            )
    for path, count in _rate_limit_hits.items():
        safe = path.replace('"', "")
        lines.append(
            f'ambulance_cdss_rate_limit_hits_total{{path="{safe}"}} {count}'
        )
    return "\n".join(lines) + "\n"
