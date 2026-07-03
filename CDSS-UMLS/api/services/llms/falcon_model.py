"""Falcon model integration."""

import logging
import time

from api.config import settings

logger = logging.getLogger(__name__)


class FalconModel:
    """Falcon model wrapper."""

    def __init__(self):
        self.api_key = settings.FALCON_API_KEY
        # TODO: Initialize Falcon API client

    async def generate(
        self,
        prompt: str,
        context: list[dict[str, str]] | None = None,
        patient_history: str | None = None,
        max_tokens: int | None = 1000,
        temperature: float = 0.7,
        **kwargs,
    ) -> dict[str, any]:
        """Generate response using Falcon model."""
        start_time = time.time()

        # Check if no evidence exists
        if not context or len(context) == 0:
            processing_time = time.time() - start_time
            return {
                "text": "Insufficient knowledge in UMLS database.",
                "confidence": 0.0,
                "processing_time": processing_time,
                "model": "falcon",
            }

        # Build prompt
        self._build_prompt(prompt, context, patient_history)

        try:
            # TODO: Implement Falcon API call
            response_text = f"[Falcon response for: {prompt}]"

            processing_time = time.time() - start_time

            return {
                "text": response_text,
                "confidence": 0.75,
                "processing_time": processing_time,
                "model": "falcon",
            }
        except Exception as e:
            logger.error(f"Falcon API error: {e}")
            raise

    def _build_prompt(
        self, prompt: str, context: list[dict[str, str]] | None, patient_history: str | None
    ) -> str:
        """Build full prompt."""
        parts = []

        # Add strict evidence-only instructions
        if context:
            context_text = "\n\n".join([doc.get("text", "") for doc in context])
            parts.append(f"""Use ONLY the following medical evidence to generate your response:

{context_text}

DO NOT cite external sources.""")

        if patient_history:
            parts.append(f"Patient History:\n{patient_history}")

        parts.append(f"Question: {prompt}")

        return "\n\n".join(parts)
