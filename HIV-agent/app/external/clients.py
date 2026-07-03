"""Reusable external clinical-endpoint client pattern.

Future integrations should follow this shape:

- Env-configured base URL and timeout.
- `httpx.AsyncClient` for network I/O.
- `retry.async_retry` for transient network failures.
- `retry.with_timeout` so no call can stall `chat_stream`.
- Graceful degradation: never raise into API handlers; return `None` on
  unavailable/parse failures and log warnings with enough detail to debug.
- Explicit return semantics: `None` means unavailable/unchecked; `[]` means the
  external source was checked and returned no matches.

The RxNorm client below is the first concrete implementation. It resolves drug
names to RxCUIs, queries RxNorm interactions, and falls back to openFDA label
search when RxNorm resolution produces no RxCUI for a medication.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from ..retry import async_retry, with_timeout

logger = logging.getLogger(__name__)

_RXNORM_BASE_URL = os.getenv(
    "CDSS_RXNORM_BASE_URL",
    "https://rxnav.nlm.nih.gov/REST",
).rstrip("/")
_OPENFDA_BASE_URL = os.getenv(
    "CDSS_OPENFDA_BASE_URL",
    "https://api.fda.gov/drug/label.json",
)
_EXTERNAL_TIMEOUT_SECONDS = float(
    os.getenv("CDSS_EXTERNAL_ENDPOINT_TIMEOUT_SECONDS", "4")
)


def _safe_detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class RxNormClient:
    """RxNorm/openFDA interaction lookup client.

    `get_interactions` returns:
    - `None` when RxNorm/openFDA is unavailable or the response cannot be parsed.
    - `[]` when medications were checked and no interactions were found.
    - `list[dict]` with sourced interaction summaries when matches exist.
    """

    def __init__(
        self,
        base_url: str = _RXNORM_BASE_URL,
        openfda_url: str = _OPENFDA_BASE_URL,
        timeout_seconds: float = _EXTERNAL_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.openfda_url = openfda_url
        self.timeout_seconds = timeout_seconds

    async def get_interactions(
        self,
        drug_names: List[str],
    ) -> Optional[List[Dict[str, Any]]]:
        medications = [str(name).strip() for name in drug_names if str(name).strip()]
        if len(medications) < 2:
            return []

        rxcuis_by_name: Dict[str, List[str]] = {}
        for medication in medications:
            rxcuis = await self._resolve_rxcuis(medication)
            if rxcuis:
                rxcuis_by_name[medication] = rxcuis

        unresolved_medications = [
            medication for medication in medications if medication not in rxcuis_by_name
        ]
        interactions: List[Dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for drug_a, cuis_a in rxcuis_by_name.items():
            for drug_b, cuis_b in rxcuis_by_name.items():
                if drug_a >= drug_b:
                    continue
                pair_interactions = await self._interactions_for_pair(
                    drug_a, cuis_a, drug_b, cuis_b
                )
                for item in pair_interactions:
                    key = (
                        str(item.get("drug_a", "")).lower(),
                        str(item.get("drug_b", "")).lower(),
                        str(item.get("description", "")).lower(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    interactions.append(item)

        if interactions or not unresolved_medications:
            return interactions

        fallback_interactions = await self._openfda_interactions(medications)
        if fallback_interactions is None:
            return None

        for item in fallback_interactions:
            key = (
                str(item.get("drug_a", "")).lower(),
                str(item.get("drug_b", "")).lower(),
                str(item.get("description", "")).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            interactions.append(item)
        return interactions

    async def _resolve_rxcuis(self, medication: str) -> List[str]:
        async def _fetch() -> List[str]:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}/rxcui.json",
                    params={"name": medication},
                )
            response.raise_for_status()
            payload = response.json()
            id_group = payload.get("idGroup", {})
            rxnorm_ids = id_group.get("rxnormId", [])
            return [str(cui) for cui in rxnorm_ids]

        try:
            return await with_timeout(
                async_retry(_fetch, max_attempts=2),
                self.timeout_seconds,
            )
        except Exception as exc:
            logger.warning("RxNorm RxCUI resolution failed for %s: %s", medication, exc)
            return []

    async def _interactions_for_pair(
        self,
        drug_a: str,
        cuis_a: List[str],
        drug_b: str,
        cuis_b: List[str],
    ) -> List[Dict[str, Any]]:
        interactions: List[Dict[str, Any]] = []
        for cui_a in cuis_a:
            for cui_b in cuis_b:
                pair = await self._rxnorm_interaction_for_cuis(cui_a, cui_b)
                for item in pair:
                    item.setdefault("drug_a", drug_a)
                    item.setdefault("drug_b", drug_b)
                    interactions.append(item)
        return interactions

    async def _rxnorm_interaction_for_cuis(
        self,
        cui_a: str,
        cui_b: str,
    ) -> List[Dict[str, Any]]:
        async def _fetch() -> List[Dict[str, Any]]:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}/interaction.json",
                    params={"rxcui": [cui_a, cui_b]},
                )
            response.raise_for_status()
            payload = response.json()
            interaction_groups = payload.get("interactionTypeGroup", [])
            interactions: List[Dict[str, Any]] = []
            for group in interaction_groups:
                for interaction_type in group.get("interactionType", []):
                    for interaction in interaction_type.get("interaction", []):
                        interactions.append(
                            {
                                "source": "RxNorm",
                                "severity": interaction_type.get("severity", "unknown"),
                                "description": interaction.get("description", ""),
                            }
                        )
            return interactions

        try:
            return await with_timeout(
                async_retry(_fetch, max_attempts=2),
                self.timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "RxNorm interaction lookup failed for %s/%s: %s",
                cui_a,
                cui_b,
                _safe_detail(exc),
            )
            return []

    async def _openfda_interactions(
        self,
        medications: List[str],
    ) -> Optional[List[Dict[str, Any]]]:
        searched = []
        interactions: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for medication in medications:
            label = await self._openfda_label(medication)
            if label is None:
                continue
            searched.append(medication)
            text = " ".join(
                str(label.get(field, ""))
                for field in ("warnings", "adverse_reactions", "drug_interactions")
            ).lower()
            for other in medications:
                if other == medication:
                    continue
                if other.lower() in text:
                    key = tuple(sorted([medication.lower(), other.lower()]))
                    if key in seen:
                        continue
                    seen.add(":".join(key))
                    interactions.append(
                        {
                            "source": "openFDA",
                            "severity": "review_label",
                            "drug_a": medication,
                            "drug_b": other,
                            "description": "openFDA label text mentions the co-medication; review the label before use.",
                        }
                    )

        if not searched:
            return None
        return interactions

    async def _openfda_label(self, medication: str) -> Optional[Dict[str, Any]]:
        async def _fetch() -> Optional[Dict[str, Any]]:
            query = quote(f'openfda.generic_name:"{medication}" OR openfda.brand_name:"{medication}"')
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    self.openfda_url,
                    params={"search": query, "limit": "1"},
                )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", [])
            return results[0] if results else None

        try:
            return await with_timeout(
                async_retry(_fetch, max_attempts=2),
                self.timeout_seconds,
            )
        except Exception as exc:
            logger.warning("openFDA label lookup failed for %s: %s", medication, exc)
            return None
