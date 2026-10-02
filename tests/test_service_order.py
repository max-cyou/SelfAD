import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class ServiceOrderTests(unittest.TestCase):
    def test_service_views_use_creation_order(self):
        participants = (
            PROJECT_DIR / "selfad" / "routes" / "participants.py"
        ).read_text(encoding="utf-8")
        admin = (
            PROJECT_DIR / "selfad" / "routes" / "admin_shared.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(participants.count(".order_by(Service.id)"), 2)
        self.assertIn("select(Service).order_by(Service.id)", admin)


if __name__ == "__main__":
    unittest.main()
