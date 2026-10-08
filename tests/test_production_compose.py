import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


class ProductionComposeTests(unittest.TestCase):
    def test_control_plane_is_only_exposed_through_caddy(self):
        compose = (PROJECT_DIR / "docker-compose.production.yml").read_text(
            encoding="utf-8"
        )
        caddyfile = (PROJECT_DIR / "docker" / "Caddyfile").read_text(
            encoding="utf-8"
        )
        self.assertIn("caddy:", compose)
        self.assertIn("SELFAD_TRUSTED_PROXY_HOSTS: 172.30.0.2", compose)
        self.assertIn('SELFAD_PUBLIC_EVENT_MODE: "true"', compose)
        self.assertIn("SELFAD_RUNNER_ISOLATION:", compose)
        self.assertNotIn('SELFAD_HTTP_PORT:-8000}:8000', compose)
        self.assertNotIn('SELFAD_GITEA_HTTP_PORT:-8929}:8929', compose)
        self.assertIn("reverse_proxy selfad:8000", caddyfile)
        self.assertIn("reverse_proxy selfad:8929", caddyfile)
        self.assertIn("forward_auth selfad:8000", caddyfile)
        self.assertIn("/internal/gitea-public-access", caddyfile)
        self.assertIn("Strict-Transport-Security", caddyfile)
        self.assertEqual(caddyfile.count("import security_headers"), 2)


if __name__ == "__main__":
    unittest.main()
