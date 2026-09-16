import json
import unittest
from unittest.mock import MagicMock, patch

from selfad.routes import health


class ReadinessTests(unittest.TestCase):
    @patch("selfad.routes.health.runner_is_available", return_value=True)
    @patch("selfad.routes.health.get_authenticated_user")
    @patch("selfad.routes.health.engine")
    def test_ready_requires_database_gitea_and_runner(
        self, engine, _gitea_user, _runner
    ):
        connection = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection

        response = health.readiness()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {
            "status": "ok",
            "database": True,
            "gitea": True,
            "runner": True,
            "runner_mode": "unavailable",
        })
        connection.execute.assert_called_once()

    @patch("selfad.routes.health.runner_is_available", return_value=True)
    @patch("selfad.routes.health.get_authenticated_user")
    @patch("selfad.routes.health.engine")
    def test_database_failure_marks_readiness_degraded(
        self, engine, _gitea_user, _runner
    ):
        engine.connect.side_effect = OSError("database unavailable")

        response = health.readiness()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.body)["database"])


if __name__ == "__main__":
    unittest.main()
