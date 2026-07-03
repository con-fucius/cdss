"""OpenAI model integration (GPT-4, GPT-3.5, etc.)."""

import logging
import time

from openai import OpenAI

from api.config import settings

logger = logging.getLogger(__name__)


class OpenAIModel:
    """OpenAI GPT model wrapper."""

    def __init__(self, model_name: str = "gpt-4"):
        self.model_name = model_name
        api_key = settings.OPENAI_API_KEY

        # Validate API key
        if not api_key or len(api_key.strip()) == 0:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please set it in your .env file or environment variables."
            )

        # Strip any whitespace that might have been accidentally added
        api_key = api_key.strip()

        # Log key status (without exposing the key)
        if api_key.startswith("sk-"):
            logger.info(
                f"OpenAI API key loaded (length: {len(api_key)}, starts with: {api_key[:10]}...)"
            )
        else:
            logger.warning(
                f"OpenAI API key format unexpected (starts with: {api_key[:10] if len(api_key) > 10 else api_key}...)"
            )

        self.client = OpenAI(api_key=api_key)

    async def generate(
        self,
        prompt: str,
        context: list[dict[str, str]] | None = None,
        patient_history: str | None = None,
        max_tokens: int | None = 1000,
        temperature: float = 0.7,
        **kwargs,
    ) -> dict[str, any]:
        """Generate response using OpenAI API."""
        start_time = time.time()

        # Check if no evidence exists
        if not context or len(context) == 0:
            processing_time = time.time() - start_time
            return {
                "text": "Insufficient knowledge in UMLS database.",
                "confidence": 0.0,
                "processing_time": processing_time,
                "model": self.model_name,
            }

        # Build messages
        messages = []

        # System prompt with strict evidence-only instructions
        system_prompt = self._build_system_prompt()
        messages.append({"role": "system", "content": system_prompt})

        # Add context with strict instructions to use ONLY this evidence
        context_text = "\n\n".join([doc.get("text", "") for doc in context])
        messages.append(
            {
                "role": "system",
                "content": f"""Use ONLY the following medical evidence to generate your response:

{context_text}

DO NOT cite external sources.""",
            }
        )

        # Add patient history
        if patient_history:
            messages.append({"role": "system", "content": f"Patient history: {patient_history}"})

        # User prompt
        messages.append({"role": "user", "content": prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            processing_time = time.time() - start_time

            return {
                "text": response.choices[0].message.content,
                "confidence": 0.8,  # Placeholder
                "processing_time": processing_time,
                "model": self.model_name,
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"OpenAI API error: {error_msg}")

            # Provide helpful error messages
            if (
                "401" in error_msg
                or "unauthorized" in error_msg.lower()
                or "api key" in error_msg.lower()
            ):
                logger.error(
                    "OpenAI API key issue detected. "
                    "Please check:\n"
                    "1. OPENAI_API_KEY is set in .env file\n"
                    "2. The key is valid and not expired\n"
                    "3. You've restarted the API after updating .env\n"
                    f"Current key length: {len(settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else 0}"
                )

            raise

    def _build_system_prompt(self) -> str:
        """Build system prompt for clinical decision support."""
        return """You are a clinical decision support assistant powered by UMLS medical terminology.
        Provide evidence-based recommendations using ONLY the medical evidence provided to you.
        Do not cite external sources or make up information not present in the provided evidence.
        Consider patient safety in all recommendations."""
