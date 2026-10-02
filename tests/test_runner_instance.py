import os
import unittest
from unittest.mock import patch

from selfad.runner import CommandResult, cleanup_managed_runner_resources


class RunnerInstanceTests(unittest.TestCase):
    @patch("selfad.runner._docker_quiet")
    @patch("selfad.runner._docker")
    def test_cleanup_is_scoped_to_this_installation(self, docker, _docker_quiet):
        docker.side_effect = [
            CommandResult(0, "25.0"),
            CommandResult(0, ""),
            CommandResult(0, ""),
            CommandResult(0, ""),
        ]
        with patch.dict(
            os.environ,
            {"SELFAD_RUNNER_INSTANCE_ID": "public-event-01"},
        ):
            cleanup_managed_runner_resources()
        for call in docker.call_args_list[1:]:
            self.assertIn(
                "label=selfad.instance=public-event-01",
                call.args[0],
            )


if __name__ == "__main__":
    unittest.main()
