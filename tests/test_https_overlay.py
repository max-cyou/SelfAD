import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class HttpsOverlayTests(unittest.TestCase):
    def test_overlay_trusts_the_bundled_caddy_only(self):
        overlay = (PROJECT_DIR / "compose.https.yaml").read_text(encoding="utf-8")
        self.assertIn("SELFAD_TRUST_PROXY_HEADERS: \"true\"", overlay)
        self.assertIn("SELFAD_TRUSTED_PROXY_HOSTS: 172.31.255.2", overlay)
        self.assertIn("ipv4_address: 172.31.255.2", overlay)
        self.assertIn("image: caddy:2.10-alpine", overlay)
        self.assertIn("./docker/Caddyfile:/etc/caddy/Caddyfile:ro", overlay)

    def test_overlay_publishes_tls_ports_and_requires_domains(self):
        overlay = (PROJECT_DIR / "compose.https.yaml").read_text(encoding="utf-8")
        self.assertIn("- \"80:80\"", overlay)
        self.assertIn("- \"443:443\"", overlay)
        self.assertIn(
            "SELFAD_PUBLIC_DOMAIN: ${SELFAD_PUBLIC_DOMAIN:?",
            overlay,
        )
        self.assertIn(
            "SELFAD_GITEA_DOMAIN: ${SELFAD_GITEA_DOMAIN:?",
            overlay,
        )

    def test_overlay_keeps_selfad_on_the_default_runner_network(self):
        overlay = (PROJECT_DIR / "compose.https.yaml").read_text(encoding="utf-8")
        selfad_section = overlay.split("  caddy:", 1)[0]
        self.assertIn("default: {}", selfad_section)
        self.assertIn("edge:", selfad_section)


class PinnedGatewayTests(unittest.TestCase):
    def test_default_network_gateway_is_pinned_and_trusted_by_default(self):
        compose = (PROJECT_DIR / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("- subnet: 172.31.201.0/24", compose)
        self.assertIn(
            "SELFAD_TRUSTED_PROXY_HOSTS: ${SELFAD_TRUSTED_PROXY_HOSTS:-172.31.201.1}",
            compose,
        )


if __name__ == "__main__":
    unittest.main()
