"""Redis-backed caching with graceful degradation."""
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_redis_client = None

try:
    import redis
    _redis_client = redis.Redis(
        host=os.getenv('REDIS_HOST', 'localhost'),
        port=int(os.getenv('REDIS_PORT', '6379')),
        db=0,
        decode_responses=True,
        socket_connect_timeout=2,
    )
    _redis_client.ping()
    logger.info('Redis connected at %s:%s', os.getenv('REDIS_HOST', 'localhost'), os.getenv('REDIS_PORT', '6379'))
except Exception as exc:
    logger.warning('Redis unavailable — running without cache: %s', exc)
    _redis_client = None


def cache_get(key: str) -> Any | None:
    if _redis_client is None:
        return None
    try:
        data = _redis_client.get(key)
        return json.loads(data) if data else None
    except Exception:
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 30) -> None:
    if _redis_client is None:
        return
    try:
        _redis_client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception:
        pass


def cache_delete(pattern: str) -> None:
    if _redis_client is None:
        return
    try:
        for key in _redis_client.scan_iter(match=pattern):
            _redis_client.delete(key)
    except Exception:
        pass


def cache_health() -> dict:
    if _redis_client is None:
        return {'status': 'unavailable', 'connected': False}
    try:
        _redis_client.ping()
        return {'status': 'ok', 'connected': True, 'keys': _redis_client.dbsize()}
    except Exception as e:
        return {'status': 'error', 'connected': False, 'error': str(e)}
