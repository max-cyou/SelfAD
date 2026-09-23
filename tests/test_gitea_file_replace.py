import unittest
from unittest.mock import patch

from selfad.gitea import replace_repository_file
from selfad.settings import GiteaSettings


class GiteaFileReplaceTests(unittest.TestCase):
    @patch("selfad.gitea._request")
    def test_existing_file_is_replaced_with_its_sha(self, request):
        request.side_effect = [{"sha": "old-file-sha"}, {}]
        settings = GiteaSettings(
            internal_url="http://gitea.test",
            public_url="http://gitea.test",
            webhook_url="http://selfad.test/hooks/gitea",
            private_token="token",
            verify_tls=False,
        )

        replace_repository_file(
            settings,
            "root/notes",
            "README.md",
            content=b"local run",
            branch="main",
            message="Replace guide",
        )

        self.assertEqual(request.call_args_list[0].args[1], "GET")
        self.assertEqual(request.call_args_list[1].args[1], "PUT")
        self.assertEqual(
            request.call_args_list[1].args[3]["sha"],
            "old-file-sha",
        )


if __name__ == "__main__":
    unittest.main()
