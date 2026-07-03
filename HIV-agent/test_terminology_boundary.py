import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch


class TerminologyBoundaryTests(unittest.TestCase):
    def test_expand_query_with_terminology_normal_expansion(self):
        from app.terminology.service import expand_query_with_terminology_details

        class FakeService:
            async def link_text(self, text, disease=None):
                return [{"preferred_name": "Human immunodeficiency virus infection"}]

        async def run():
            expanded, concepts = await expand_query_with_terminology_details("ART options", "hiv")
            return expanded, concepts

        with patch("app.terminology.service.TerminologyService", return_value=FakeService()):
            expanded, concepts = asyncio.run(run())

        self.assertIn("Human immunodeficiency virus infection", expanded)
        self.assertEqual(len(concepts), 1)

    def test_expand_query_with_terminology_no_concepts_returns_original(self):
        from app.terminology.service import expand_query_with_terminology

        class FakeService:
            async def link_text(self, text, disease=None):
                return []

        async def run():
            return await expand_query_with_terminology("hello", "hiv")

        with patch("app.terminology.service.TerminologyService", return_value=FakeService()):
            expanded = asyncio.run(run())

        self.assertEqual(expanded, "hello")

    def test_expand_query_with_terminology_timeout_returns_original(self):
        from app.terminology.service import expand_query_with_terminology

        class FakeService:
            async def link_text(self, text, disease=None):
                raise TimeoutError()

        async def run():
            return await expand_query_with_terminology("hello", "hiv")

        with patch("app.terminology.service.TerminologyService", return_value=FakeService()):
            expanded = asyncio.run(run())

        self.assertEqual(expanded, "hello")

    def test_shadow_telemetry_is_opt_in(self):
        api_py = Path("app/api.py").read_text(encoding="utf-8")

        self.assertIn('CDSS_TERMINOLOGY_SHADOW_ENABLED", "false"', api_py)
        self.assertIn("if TERMINOLOGY_SHADOW_ENABLED:", api_py)
        self.assertIn("TERMINOLOGY_SHADOW_RETRIEVAL_ENABLED", api_py)

    def test_qdrant_ids_increment_for_new_cuis(self):
        embed_py = Path("app/terminology/embed.py").read_text(encoding="utf-8")

        self.assertIn("cui_to_id[cui] = _next_id_ref[0]", embed_py)
        self.assertNotIn("cui_to_id[cui] = next_id", embed_py)

    def test_annotator_dry_run_does_not_report_writes(self):
        annotator = Path("scripts/annotate_kb.py").read_text(encoding="utf-8")

        self.assertIn("Dry run: would upsert", annotator)
        self.assertIn("return 0", annotator)
        self.assertNotIn("Progress is checkpointed", annotator)

    def test_followup_migration_hardens_nullable_relation_source(self):
        migration = Path("alembic/versions/0006_terminology_hardening.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("UPDATE terminology_relations SET source_sab = ''", migration)
        self.assertIn("nullable=False", migration)
        self.assertIn("'[]'::jsonb", migration)

    def test_search_deduplicates_without_postgres_distinct_on(self):
        service_py = Path("app/terminology/service.py").read_text(encoding="utf-8")

        self.assertIn("seen_cuis = set()", service_py)
        self.assertIn("if concept.cui in seen_cuis:", service_py)
        self.assertNotIn(".distinct(TerminologyConcept.cui)", service_py)

    def test_etl_stores_tui_codes_and_names_for_semantic_filters(self):
        from app.terminology.etl import _semantic_type_values

        values = _semantic_type_values(
            {
                "semantic_types": ["Disease or Syndrome"],
                "semantic_type_details": [
                    {"tui": "T047", "semantic_type": "Disease or Syndrome"},
                    {"tui": "T121", "semantic_type": "Pharmacologic Substance"},
                ],
            }
        )

        self.assertIn("Disease or Syndrome", values)
        self.assertIn("T047", values)
        self.assertIn("T121", values)

    def test_etl_alias_source_is_not_nullable(self):
        etl_py = Path("app/terminology/etl.py").read_text(encoding="utf-8")

        self.assertIn('"source_sab": ""', etl_py)
        self.assertNotIn('"source_sab": None', etl_py)


if __name__ == "__main__":
    unittest.main()
