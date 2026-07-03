import unittest

from app.phase7 import PHASE7_STATUS


class Phase7Tests(unittest.TestCase):
    def test_phase7_is_deleted_not_skipped(self):
        self.assertEqual(PHASE7_STATUS["status"], "deleted")
        self.assertIn("Groq/Puter", PHASE7_STATUS["reason"])


if __name__ == "__main__":
    unittest.main()
