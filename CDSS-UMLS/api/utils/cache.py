"""
Caching utilities using Redis
"""
from typing import Optional, Any
import json
import redis
from api.config import settings
import logging

logger = logging.getLogger(__name__)


class Cache:
    """Redis-based cache"""
    
    def __init__(self):
        try:
            self.client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True
            )
            self.ttl = settings.CACHE_TTL
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Cache disabled.")
            self.client = None
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if not self.client:
            return None
        
        try:
            value = self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in cache"""
        if not self.client:
            return
        
        try:
            serialized = json.dumps(value)
            ttl = ttl or self.ttl
            self.client.setex(key, ttl, serialized)
        except Exception as e:
            logger.error(f"Cache set error: {e}")
    
    def delete(self, key: str):
        """Delete key from cache"""
        if not self.client:
            return
        
        try:
            self.client.delete(key)
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
    
    def clear(self):
        """Clear all cache"""
        if not self.client:
            return
        
        try:
            self.client.flushdb()
        except Exception as e:
            logger.error(f"Cache clear error: {e}")


cache = Cache()

