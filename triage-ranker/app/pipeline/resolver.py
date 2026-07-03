"""
triage-ranker/app/pipeline/resolver.py

Stage 2 — UMLS resolution with four-layer cache.

L1: In-process TTLCache (1000 entries, 1 hour TTL using cachetools)
L2: SQLite persistent cache (cache/umls_cache.db, survives restarts)
L3: UMLS REST API (3s timeout, only called on L1+L2 miss, only if
    UMLS_API_KEY configured)
L4: Fallback from clinical_rules.yaml icd10_prefix/snomed_hint fields

On L3 success: write to L2 then L1.
On L3 failure or no key: use L4, set degraded_mode=True on response.

Design constraints:
- Never blocks Stage 3
- UMLS API key absence handled at config level, not scattered through
  pipeline code
- All external HTTP calls return None/empty on failure, never raise
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from cachetools import TTLCache

from ..config import get_umls_api_key, get_umls_api_timeout, is_umls_configured

logger = logging.getLogger(__name__)

# L1: In-process TTLCache
_l1_cache: TTLCache = TTLCache(maxsize=1000, ttl=3600)

# L2: SQLite connection (lazy init)
_l2_conn: Optional[sqlite3.Connection] = None


def _init_l2_cache(db_path: str) -> None:
    """Initialize the SQLite L2 cache database."""
    global _l2_conn
    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        _l2_conn = sqlite3.connect(db_path)
        _l2_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS umls_cache (
                term TEXT PRIMARY KEY,
                cui TEXT,
                snomed_code TEXT,
                icd10_code TEXT,
                semantic_type TEXT,
                resolved_at TEXT
            )
            """
        )
        _l2_conn.commit()
        logger.info("L2 UMLS cache initialized at %s", db_path)
    except Exception as exc:
        logger.warning("L2 cache init failed: %s", exc)
        _l2_conn = None


def _l2_get(term: str) -> Optional[Dict[str, Any]]:
    """Look up a term in the L2 SQLite cache."""
    if _l2_conn is None:
        return None
    try:
        cursor = _l2_conn.execute(
            "SELECT cui, snomed_code, icd10_code, semantic_type FROM umls_cache WHERE term = ?",
            (term,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "cui": row[0],
                "snomed_code": row[1],
                "icd10_code": row[2],
                "semantic_type": row[3],
            }
    except Exception as exc:
        logger.warning("L2 cache lookup failed for %s: %s", term, exc)
    return None


def _l2_put(term: str, result: Dict[str, Any]) -> None:
    """Write a term to the L2 SQLite cache."""
    if _l2_conn is None:
        return
    try:
        _l2_conn.execute(
            """
            INSERT OR REPLACE INTO umls_cache
            (term, cui, snomed_code, icd10_code, semantic_type, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                term,
                result.get("cui"),
                result.get("snomed_code"),
                result.get("icd10_code"),
                result.get("semantic_type"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _l2_conn.commit()
    except Exception as exc:
        logger.warning("L2 cache write failed for %s: %s", term, exc)


async def _l3_umls_api(term: str) -> Optional[Dict[str, Any]]:
    """
    L3: UMLS REST API lookup. 3s timeout.
    Returns None on any failure — never raises.
    """
    if not is_umls_configured():
        return None

    api_key = get_umls_api_key()
    timeout = get_umls_api_timeout()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                "https://uts-ws.nlm.nih.gov/rest/search/current",
                params={
                    "string": term,
                    "apiKey": api_key,
                    "maxResults": 1,
                },
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("result", {}).get("results", [])
            if not results:
                return None

            r = results[0]
            return {
                "cui": r.get("ui", ""),
                "snomed_code": "",  # Would need additional API call
                "icd10_code": "",   # Would need additional API call
                "semantic_type": r.get("semanticTypes", [{}])[0].get("name", ""),
            }

    except httpx.TimeoutException:
        logger.warning("UMLS API timeout for term: %s", term)
        return None
    except Exception as exc:
        logger.warning("UMLS API failed for term %s: %s", term, exc)
        return None


def _l4_fallback(
    term: str, rules: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    L4: Fallback from clinical_rules.yaml icd10_prefix/snomed_hint.
    Uses pre-loaded rules to provide basic coding without API.
    """
    term_lower = term.lower()
    for rule in rules:
        if rule["term"].lower() == term_lower:
            return {
                "cui": "",
                "snomed_code": rule.get("snomed_hint", ""),
                "icd10_code": rule.get("icd10_prefix", ""),
                "semantic_type": rule.get("category", "UNKNOWN"),
            }
        for syn in rule.get("synonyms", []):
            if syn.lower() == term_lower:
                return {
                    "cui": "",
                    "snomed_code": rule.get("snomed_hint", ""),
                    "icd10_code": rule.get("icd10_prefix", ""),
                    "semantic_type": rule.get("category", "UNKNOWN"),
                }
    return None


async def resolve_keywords(
    keywords: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    cache_db_path: str = "cache/umls_cache.db",
) -> tuple[List[Dict[str, Any]], bool]:
    """
    Stage 2 — Resolve extracted keywords through four-layer cache.

    Args:
        keywords: Extracted keywords from Stage 1
        rules: Clinical rules from clinical_rules.yaml
        cache_db_path: Path to SQLite L2 cache

    Returns:
        Tuple of (resolved keywords, degraded_mode flag)
    """
    # Init L2 cache if needed
    if _l2_conn is None:
        _init_l2_cache(cache_db_path)

    degraded_mode = False
    resolved = []

    for kw in keywords:
        text = kw.text if hasattr(kw, "text") else kw.get("text", "")
        text_lower = text.lower()

        # L1: In-process TTLCache
        l1_result = _l1_cache.get(text_lower)
        if l1_result:
            resolved.append({**kw, "umls_resolution": l1_result})
            continue

        # L2: SQLite persistent cache (run sync I/O in thread)
        l2_result = await asyncio.to_thread(_l2_get, text_lower)
        if l2_result:
            _l1_cache[text_lower] = l2_result
            resolved.append({**kw, "umls_resolution": l2_result})
            continue

        # L3: UMLS REST API (only if configured)
        l3_result = await _l3_umls_api(text)
        if l3_result:
            # Write to L2 and L1 (sync I/O in thread)
            await asyncio.to_thread(_l2_put, text_lower, l3_result)
            _l1_cache[text_lower] = l3_result
            resolved.append({**kw, "umls_resolution": l3_result})
            continue

        # L4: Fallback from rules
        l4_result = _l4_fallback(text, rules)
        if l4_result:
            degraded_mode = True
            resolved.append({**kw, "umls_resolution": l4_result})
        else:
            # No resolution at all — still add the keyword
            degraded_mode = True
            resolved.append({**kw, "umls_resolution": None})

    return resolved, degraded_mode


def purge_caches() -> None:
    """Purge L1 and L2 caches. Called by DELETE /admin/cache."""
    global _l2_conn
    _l1_cache.clear()
    if _l2_conn is not None:
        try:
            _l2_conn.execute("DELETE FROM umls_cache")
            _l2_conn.commit()
        except Exception as exc:
            logger.warning("L2 cache purge failed: %s", exc)
    logger.info("UMLS caches purged (L1 + L2).")
