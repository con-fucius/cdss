"""app/external/llm_client.py

Optional LLM client for NLP fallback.
When TRIAGE_RANKER_BASE_URL is configured, calls the triage ranker.
When LLM_API_URL is configured, calls an external LLM.
Otherwise returns None (degraded mode)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self) -> None:
        self.api_url = os.getenv("LLM_API_URL", "")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "10"))

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url)

    async def extract_entities(self, transcript: str) -> dict[str, Any] | None:
        """Use LLM to extract clinical entities from transcript.
        Returns None if not configured or on error."""
        if not self.is_configured:
            return None

        prompt = (
            "Extract clinical entities from this emergency dispatch transcript. "
            "Return JSON with:\n"
            "- chief_complaint: the main medical issue\n"
            "- entities: list of {label, category, negated, severity_weight}\n"
            "- vitals: extracted vital signs "
            "(bp_systolic, bp_diastolic, heart_rate, respiratory_rate, "
            "spo2, temperature, gcs_total)\n"
            "- location_text: any location mentioned\n"
            "- confidence: 0-1 score\n\n"
            f"Transcript: {transcript}"
        )

        try:
            import httpx
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers: dict[str, str] = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a medical NLP assistant. Extract "
                                    "clinical entities from emergency dispatch "
                                    "transcripts. Return only valid JSON."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    # Try to parse JSON from response, strip markdown fences
                    text = content.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[-1]
                        if text.endswith("```"):
                            text = text[:-3]
                    return json.loads(text.strip())
        except Exception as exc:
            logger.warning("LLM extraction failed: %s", exc)
        return None
