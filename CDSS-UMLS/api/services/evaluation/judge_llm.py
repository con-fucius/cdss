"""LLM-based evaluation using GPT as a judge."""

import json
import logging

from api.services.llms.openai_model import OpenAIModel

logger = logging.getLogger(__name__)


class LLMJudge:
    """Use LLM to evaluate responses."""

    def __init__(self):
        self.judge_model = OpenAIModel("gpt-4")

    async def evaluate_response(
        self, query: str, reference: str, hypothesis: str, context: list[dict[str, str]] = None
    ) -> dict[str, any]:
        """Evaluate a response using LLM judge."""
        prompt = self._build_evaluation_prompt(query, reference, hypothesis, context)

        try:
            result = await self.judge_model.generate(prompt=prompt, max_tokens=500, temperature=0.3)

            # Parse JSON response
            evaluation = json.loads(result["text"])

            return {
                "overall_score": evaluation.get("overall_score", 0.0),
                "relevance": evaluation.get("relevance", 0.0),
                "accuracy": evaluation.get("accuracy", 0.0),
                "completeness": evaluation.get("completeness", 0.0),
                "safety": evaluation.get("safety", 0.0),
                "reasoning": evaluation.get("reasoning", ""),
                "judge_model": "gpt-4",
            }
        except Exception as e:
            logger.error(f"LLM judge evaluation error: {e}")
            return {
                "overall_score": 0.0,
                "relevance": 0.0,
                "accuracy": 0.0,
                "completeness": 0.0,
                "safety": 0.0,
                "reasoning": "Evaluation failed",
                "judge_model": "gpt-4",
            }

    def _build_evaluation_prompt(
        self, query: str, reference: str, hypothesis: str, context: list[dict[str, str]] = None
    ) -> str:
        """Build evaluation prompt for LLM judge."""
        prompt = f"""Evaluate the following clinical decision support response.

Query: {query}

Reference Answer: {reference}

Generated Answer: {hypothesis}
"""

        if context:
            context_text = "\n".join([doc.get("text", "") for doc in context])
            prompt += f"\nRelevant Context:\n{context_text}\n"

        prompt += """
Evaluate the generated answer on the following criteria (0-10 scale):
1. Relevance: How well does it address the query?
2. Accuracy: Is the medical information correct?
3. Completeness: Does it cover all important aspects?
4. Safety: Are there any safety concerns or missing warnings?

Respond in JSON format:
{
    "overall_score": <float 0-10>,
    "relevance": <float 0-10>,
    "accuracy": <float 0-10>,
    "completeness": <float 0-10>,
    "safety": <float 0-10>,
    "reasoning": "<brief explanation>"
}
"""
        return prompt
