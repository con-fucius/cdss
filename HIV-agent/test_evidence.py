import unittest

from app.evidence import load_seed, normalise_graph_seed, score_graph_hit


class EvidenceGraphTests(unittest.TestCase):
    def test_hiv_seed_has_valid_edges(self):
        seed = load_seed("hiv")
        refs = {node["ref_id"] for node in seed["nodes"]}

        self.assertGreaterEqual(len(seed["nodes"]), 5)
        self.assertGreaterEqual(len(seed["edges"]), 4)
        self.assertTrue(all(edge["source_ref"] in refs for edge in seed["edges"]))
        self.assertTrue(all(edge["target_ref"] in refs for edge in seed["edges"]))

    def test_normalise_graph_seed_drops_invalid_edges(self):
        seed = {
            "nodes": [{"ref_id": "a"}, {"ref_id": "b"}],
            "edges": [
                {"source_ref": "a", "target_ref": "b"},
                {"source_ref": "a", "target_ref": "missing"},
            ],
        }

        normalised = normalise_graph_seed(seed)

        self.assertEqual(len(normalised["edges"]), 1)

    def test_score_graph_hit_ranks_matching_terms(self):
        score = score_graph_hit(
            "first line dolutegravir",
            {"label": "Use first-line ART regimen", "node_type": "decision", "payload": {}},
            {"relation_type": "includes"},
            {"label": "Dolutegravir", "node_type": "drug", "payload": {}},
        )

        self.assertGreater(score, 0.5)


if __name__ == "__main__":
    unittest.main()
