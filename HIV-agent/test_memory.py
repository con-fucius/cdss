import unittest
from unittest.mock import AsyncMock, patch

from app.memory import (
    distill_session_candidates,
    deterministic_session_facts,
    embedding_cache_key,
    patient_ref_from_context,
    _DISTILL_MODELS,
)


class MemoryTests(unittest.TestCase):
    def test_patient_memory_key_is_hashed(self):
        context = {
            "name": "Jane Patient",
            "dob": "1980-01-01",
            "active_conditions": ["hiv"],
        }

        key = patient_ref_from_context(context)

        self.assertNotIn("Jane", key)
        self.assertNotIn("1980", key)
        self.assertEqual(len(key), 64)

    def test_embedding_cache_key_includes_model(self):
        first = embedding_cache_key("malaria treatment", "model-a")
        second = embedding_cache_key("malaria treatment", "model-b")

        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_deterministic_session_facts_extracts_pending_candidates(self):
        messages = [
            {"role": "user", "content": "Patient has CD4 250 and viral load 1000."},
            {"role": "assistant", "content": "Recommend continue TDF DTG and review viral load."},
        ]

        facts = deterministic_session_facts(messages)

        self.assertTrue(facts)
        self.assertTrue({fact["fact_type"] for fact in facts} & {"lab_result", "decision", "drug_change"})
        self.assertTrue(all("approved_by" not in fact for fact in facts))

    def test_deterministic_session_facts_ignore_guideline_source_passages(self):
        messages = [
            {"role": "user", "content": "Patient has viral load 1000 and we plan adherence review."},
            {
                "role": "assistant",
                "content": (
                    "A guideline passage says viral load test will be done after "
                    "3 months of good adherence to see if ART can be continued or changed."
                ),
            },
        ]

        facts = deterministic_session_facts(messages)
        texts = [fact["fact_text"] for fact in facts]

        self.assertIn("viral load 1000 and we plan adherence review", texts)
        self.assertTrue(all("continued or" not in text for text in texts))
        self.assertTrue(all("3 months of good adherence" not in text for text in texts))

    def test_distill_models_are_not_mistral_locked(self):
        self.assertEqual(set(_DISTILL_MODELS), {"groq", "puter"})
        self.assertTrue(all("mistral" not in model.lower() for model in _DISTILL_MODELS.values()))

    def test_distill_session_candidates_deduplicates_existing_memory(self):
        messages = [
            {"role": "user", "content": "Patient has viral load 1000 and we plan adherence review."}
        ]
        existing = {
            "fact_type": "lab_result",
            "fact_text": "viral load 1000 and we plan adherence review",
        }

        with (
            patch("app.repositories.get_session_messages", new=AsyncMock(return_value=messages)),
            patch("app.repositories.list_pending_memory", new=AsyncMock(return_value=[existing])),
            patch("app.repositories.list_long_term_memory", new=AsyncMock(return_value=[])),
            patch("app.repositories.create_pending_memory", new=AsyncMock()) as create_mock,
        ):
            returned = __import__("asyncio").run(
                distill_session_candidates("s1", {"condition": "HIV"})
            )

        self.assertEqual(returned, [existing])
        create_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
