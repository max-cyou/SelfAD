import os
import unittest
from unittest.mock import patch

from selfad.settings import get_trusted_proxy_hosts


class ProxySettingsTests(unittest.TestCase):
    def test_proxy_headers_are_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_trusted_proxy_hosts())

    def test_proxy_headers_need_explicit_hosts(self):
        with patch.dict(
            os.environ,
            {"SELFAD_TRUST_PROXY_HEADERS": "true"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "TRUSTED_PROXY_HOSTS"):
                get_trusted_proxy_hosts()

    def test_proxy_headers_parse_explicit_hosts(self):
        with patch.dict(
            os.environ,
            {
                "SELFAD_TRUST_PROXY_HEADERS": "true",
                "SELFAD_TRUSTED_PROXY_HOSTS": "127.0.0.1, 10.0.0.2",
            },
            clear=True,
        ):
            self.assertEqual(
                get_trusted_proxy_hosts(),
                ["127.0.0.1", "10.0.0.2"],
            )


if __name__ == "__main__":
    unittest.main()
