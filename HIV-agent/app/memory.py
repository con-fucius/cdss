"""Approval-gated clinical memory services.

Fix: _llm_distill now uses an explicitly cheap model for each provider
instead of get_llm_model(), which returns the expensive reasoning model.
Running the expensive model over 80-message sessions daily at clinic
scale would be both costly and slower than necessary for fact extraction.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import httpx

from .config import get_embedding_model_name
from .logs import _hash_patient_ref
from .providers import (
    get_llm_provider,
    provider_auth_header,
    provider_chat_endpoint,
    provider_has_credentials,
)

_EMBEDDING_CACHE_LIMIT = 256
_embedding_lru: OrderedDict[str, List[float]] = OrderedDict()

# Cheap models used for memory distillation — never the expensive reasoning model
_DISTILL_MODELS: Dict[str, str] = {
    "groq": "llama-3.1-8b-instant",
    "puter": "openai/gpt-4o-mini",
}


def patient_ref_from_context(patient_context: Optional[Dict[str, Any]]) -> str:
    """Hash patient context before it can be used as a memory key."""
    return _hash_patient_ref(patient_context)


def embedding_cache_key(query: str, model_name: Optional[str] = None) -> str:
    payload = f"{model_name or get_embedding_model_name()}\n{query}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def get_cached_embedding(
    query: str, model_name: Optional[str] = None
) -> Optional[List[float]]:
    key = embedding_cache_key(query, model_name)
    if key in _embedding_lru:
        value = _embedding_lru.pop(key)
        _embedding_lru[key] = value
        return value

    from .repositories import get_embedding_cache

    row = await get_embedding_cache(key)
    if row is None:
        return None
    _remember_embedding(key, row)
    return row


async def put_cached_embedding(
    query: str,
    embedding: List[float],
    model_name: Optional[str] = None,
) -> None:
    key = embedding_cache_key(query, model_name)
    _remember_embedding(key, embedding)
    from .repositories import put_embedding_cache

    await put_embedding_cache(key, model_name or get_embedding_model_name(), embedding)


def _remember_embedding(key: str, embedding: List[float]) -> None:
    if key in _embedding_lru:
        _embedding_lru.pop(key)
    _embedding_lru[key] = embedding
    while len(_embedding_lru) > _EMBEDDING_CACHE_LIMIT:
        _embedding_lru.popitem(last=False)


def deterministic_session_facts(
    messages: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Extract conservative memory candidates without an LLM call."""
    joined = "\n".join(
        f"{m.get('role', '')}: {m.get('content', '')}"
        for m in messages
        if m.get("content") and m.get("role") == "user"
    )
    candidates: List[Dict[str, Any]] = []
    patterns = [
        (
            "drug_change",
            r"\b(?:start|started|stop|stopped|switch|changed|continue|continued)"
            r"\b[^.\n]{0,180}",
        ),
        (
            "lab_result",
            r"\b(?:cd4|viral load|hba1c|egfr|creatinine|blood pressure|bp)"
            r"\b[^.\n]{0,180}",
        ),
        (
            "decision",
            r"\b(?:plan|decided|recommend|recommended|follow up|review)"
            r"\b[^.\n]{0,180}",
        ),
    ]
    seen: set = set()
    for fact_type, pattern in patterns:
        for match in re.finditer(pattern, joined, flags=re.IGNORECASE):
            fact_text = re.sub(r"\s+", " ", match.group(0)).strip(" :-")
            fact_text = fact_text.strip(" .,:;!?\"'“”")
            key = (fact_type, fact_text.lower())
            if len(fact_text) < 16 or len(fact_text.split()) < 4 or key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "fact_type": fact_type,
                    "fact_text": fact_text,
                    "source_message_ids": [],
                }
            )
    return candidates[:12]


def _normalize_fact_type(fact_type: str) -> str:
    normalized = str(fact_type or "decision").strip().lower()
    replacements = {
        "lab results": "lab_result",
        "lab_result": "lab_result",
        "lab": "lab_result",
        "clinical decisions": "decision",
        "clinical decision": "decision",
        "drug changes": "drug_change",
        "drug change": "drug_change",
    }
    return replacements.get(normalized, normalized)


async def distill_session_candidates(
    session_id: str,
    patient_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Distill a session into pending memory candidates; never approves them."""
    from .repositories import (
        create_pending_memory,
        get_session_messages,
        list_long_term_memory,
        list_pending_memory,
    )

    messages = await get_session_messages(session_id, limit=80)
    if not messages:
        return []

    facts = await _llm_distill(messages)
    if not facts:
        facts = deterministic_session_facts(messages)

    patient_ref_hash = patient_ref_from_context(patient_context)
    existing_pending = await list_pending_memory(
        patient_ref_hash=patient_ref_hash,
        session_id=session_id,
    )
    existing_approved = await list_long_term_memory(patient_ref_hash)
    seen_facts = {
        (
            _normalize_fact_type(row.get("fact_type", "")),
            str(row.get("fact_text", "")).strip().lower(),
        )
        for row in [*existing_pending, *existing_approved]
    }
    existing_text_by_type: Dict[str, List[str]] = {}
    existing_texts_all: List[str] = []
    for row in [*existing_pending, *existing_approved]:
        fact_type = _normalize_fact_type(row.get("fact_type", ""))
        fact_text = str(row.get("fact_text", "")).strip().lower()
        existing_text_by_type.setdefault(fact_type, []).append(fact_text)
        if fact_text:
            existing_texts_all.append(fact_text)
    created = []
    for fact in facts:
        fact_text = str(fact.get("fact_text", "")).strip()
        fact_type = _normalize_fact_type(str(fact.get("fact_type", "decision")).strip() or "decision")
        if not fact_text:
            continue
        fact_key = (fact_type.lower(), fact_text.lower())
        if fact_key in seen_facts:
            continue
        existing_texts = existing_text_by_type.get(fact_type, [])
        normalized_fact_text = fact_text.lower()
        if any(
            normalized_fact_text in existing or existing in normalized_fact_text
            for existing in [*existing_texts_all, *existing_texts]
            if existing
        ):
            continue
        seen_facts.add(fact_key)
        created.append(
            await create_pending_memory(
                patient_ref_hash=patient_ref_hash,
                session_id=session_id,
                fact_type=fact_type,
                fact_text=fact_text,
                source_message_ids=list(fact.get("source_message_ids") or []),
            )
        )
    return [*existing_pending, *created]


async def _llm_distill(
    messages: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """
    Use the cheapest available model to extract clinical facts from a session.

    Always uses _DISTILL_MODELS[provider] — never get_llm_model() — so the
    expensive reasoning model is never burned on routine memory extraction.
    """
    provider = get_llm_provider()
    if not provider_has_credentials(provider):
        return []

    distill_model = _DISTILL_MODELS.get(provider)
    if distill_model is None:
        return []

    prompt = (
        "Extract memory candidates from this clinical support session. "
        "Return strict JSON array only — no prose, no markdown fences. "
        "Each item must have: fact_type (string), fact_text (string), "
        "source_message_ids (empty list). "
        "Include only: drug changes, lab results, clinical decisions, and "
        "open clinical questions explicitly present in the transcript. "
        "Prefer user/clinician statements. Do not extract facts from quoted "
        "guideline passages, source snippets, or generic assistant education "
        "unless the assistant states a patient-specific decision. "
        "Do not include names, dates of birth, phone numbers, addresses, "
        "or any free-text patient identifiers.\n\n"
        f"{json.dumps(messages[-80:], ensure_ascii=True)}"
    )

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                provider_chat_endpoint(provider),
                headers={
                    **provider_auth_header(provider),
                    "Content-Type": "application/json",
                },
                json={
                    "model": distill_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 800,
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
        # Strip markdown fences if the model emits them despite instructions
        raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []
