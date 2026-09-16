import unittest

from starlette.responses import Response

from selfad.application import SECURITY_HEADERS, apply_security_headers


class SecurityHeadersTests(unittest.TestCase):
    def test_headers_are_added_without_overwriting_existing_values(self):
        response = Response()
        response.headers["Referrer-Policy"] = "no-referrer"

        apply_security_headers(response)

        for name, value in SECURITY_HEADERS.items():
            expected = "no-referrer" if name == "Referrer-Policy" else value
            self.assertEqual(response.headers[name], expected)


if __name__ == "__main__":
    unittest.main()
