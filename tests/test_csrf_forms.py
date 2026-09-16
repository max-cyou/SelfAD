import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class CsrfFormTests(unittest.TestCase):
    def test_every_browser_authentication_form_includes_csrf_token(self):
        for filename in ("setup.html", "login.html", "register.html"):
            template = (
                PROJECT_DIR / "selfad" / "templates" / filename
            ).read_text(encoding="utf-8")
            self.assertIn('name="csrf_token"', template, filename)


if __name__ == "__main__":
    unittest.main()
