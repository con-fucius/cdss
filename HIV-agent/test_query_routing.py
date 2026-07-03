import unittest
from unittest.mock import patch

from app.api import (
    _build_openai_compatible_payload,
    _chat_pageindex_enabled,
    _infer_disease_from_query,
    _is_dosing_query,
    _is_smalltalk_query,
    _kb_filters_from_query,
    _resolve_retrieval_diseases,
)


class QueryRoutingTests(unittest.TestCase):
    def test_smalltalk_does_not_retrieve(self):
        self.assertTrue(_is_smalltalk_query("hi"))
        self.assertTrue(_is_smalltalk_query("thanks"))
        self.assertTrue(_is_smalltalk_query("what can you do?"))

    def test_mixed_greeting_with_clinical_question_retrieves(self):
        self.assertFalse(_is_smalltalk_query("hi, when should insulin be started?"))
        self.assertFalse(_is_smalltalk_query("hello what are BP targets?"))

    def test_obvious_disease_inference(self):
        available = ["hiv", "diabetes", "cvd", "tb", "malaria", "mental_health"]

        self.assertEqual(
            _infer_disease_from_query(
                "When should insulin be initiated in type 2 diabetes?",
                available,
            ),
            ["diabetes"],
        )
        self.assertEqual(
            _infer_disease_from_query(
                "What are the blood pressure targets for hypertension?",
                available,
            ),
            ["cvd"],
        )
        self.assertEqual(
            _infer_disease_from_query("What ART regimen is used for HIV?", available),
            ["hiv"],
        )

    def test_disease_inference_returns_ties(self):
        available = ["hiv", "diabetes", "cvd", "tb", "malaria", "mental_health"]

        self.assertEqual(
            _infer_disease_from_query(
                "How should I adjust ART when HbA1c is high?",
                available,
            ),
            ["hiv", "diabetes"],
        )

    def test_retrieval_disease_resolution_uses_context_comorbidities(self):
        from app.api import PatientContext

        available = ["hiv", "diabetes", "cvd", "tb", "malaria", "mental_health"]
        context = PatientContext(active_conditions=["hiv", "tb"])

        self.assertEqual(
            _resolve_retrieval_diseases(
                available,
                context,
                "What ART should be used?",
            ),
            ["hiv", "tb"],
        )

    def test_retrieval_disease_resolution_caps_fan_out(self):
        from app.api import PatientContext

        available = ["hiv", "diabetes", "cvd", "tb", "malaria", "mental_health"]
        context = PatientContext(active_conditions=["hiv", "tb", "malaria"])

        self.assertEqual(
            _resolve_retrieval_diseases(
                available,
                context,
                "What should I monitor?",
            ),
            ["hiv", "tb", "malaria"],
        )

    def test_dosing_query_classifier_detects_weight_based_arv_questions(self):
        self.assertTrue(_is_dosing_query("What ARV dose for a 25 kg child?"))
        self.assertTrue(_is_dosing_query("First-line regimen for weight 40 kg"))
        self.assertFalse(_is_dosing_query("When should HIV testing be repeated?"))

    def test_kb_filters_parse_weight_bands(self):
        self.assertEqual(
            _kb_filters_from_query("What ARV dose for a 25 kg child?"),
            {"weight_band": "< 30 kg"},
        )
        self.assertEqual(
            _kb_filters_from_query("First-line regimen for weight 40 kg"),
            {"weight_band": ">= 30 kg", "line": "first-line"},
        )

    def test_pageindex_is_not_injected_into_chat_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertFalse(
                _chat_pageindex_enabled(
                    "What are the blood pressure targets for hypertension?",
                    "cvd",
                )
            )

    def test_pageindex_auto_mode_requires_disease_and_clinical_query(self):
        with patch.dict("os.environ", {"CDSS_CHAT_PAGEINDEX_MODE": "auto"}, clear=False):
            self.assertFalse(_chat_pageindex_enabled("hi", "cvd"))
            self.assertFalse(
                _chat_pageindex_enabled(
                    "What are the blood pressure targets for hypertension?",
                    None,
                )
            )
            self.assertTrue(
                _chat_pageindex_enabled(
                    "What are the blood pressure targets for hypertension?",
                    "cvd",
                )
            )

    def test_groq_payload_streams_and_hides_reasoning_by_default(self):
        with patch.dict(
            "os.environ",
            {
                "QUERY_LLM_PROVIDER": "groq",
                "QUERY_LLM_MODEL": "qwen/qwen3-32b",
            },
            clear=False,
        ):
            payload = _build_openai_compatible_payload(
                provider="groq",
                query="What are the blood pressure targets?",
                context_block=None,
                retrieval_results=[],
                history=[],
            )
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["reasoning_format"], "hidden")


if __name__ == "__main__":
    unittest.main()
