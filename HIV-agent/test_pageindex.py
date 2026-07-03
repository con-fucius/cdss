import math
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.indexers.pageindex import _SUMMARISE_MODELS, PageIndexBuilder, _extractive_summary


class PageIndexTests(unittest.TestCase):
    def test_extractive_summary_is_deterministic_and_bounded(self):
        text = "First sentence about malaria treatment. Second sentence about dosing. Third sentence about follow up."
        first = _extractive_summary(text, max_chars=60)
        second = _extractive_summary(text, max_chars=60)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), len(text))
        self.assertIn("malaria treatment", first)

    def test_heading_from_multiline_text_uses_first_meaningful_line(self):
        builder = PageIndexBuilder.__new__(PageIndexBuilder)

        heading = builder._heading_from_text("\n\n  12  \n  Severe malaria treatment  \nBody")

        self.assertEqual(heading, "Severe malaria treatment")

    def test_vector_index_parameters_follow_lancedb_dimension_guidance(self):
        builder = PageIndexBuilder.__new__(PageIndexBuilder)
        table = Mock()

        builder._create_indexes(table, vector_dim=384, row_count=25)

        call = table.create_index.call_args_list[0]
        self.assertEqual(call.kwargs["num_partitions"], max(2, int(math.sqrt(25))))
        self.assertEqual(call.kwargs["num_sub_vectors"], 48)
        self.assertEqual(call.kwargs["vector_column_name"], "vector")

    def test_summarise_models_are_not_mistral_locked(self):
        self.assertEqual(set(_SUMMARISE_MODELS), {"groq", "puter"})
        self.assertTrue(all("mistral" not in model.lower() for model in _SUMMARISE_MODELS.values()))

    def test_pageindex_repair_script_exists(self):
        script = Path("scripts/build_pageindex.py").read_text(encoding="utf-8")

        self.assertIn("--missing-only", script)
        self.assertIn("PageIndexBuilder", script)
        self.assertIn("pageindex_stats", script)


if __name__ == "__main__":
    unittest.main()
