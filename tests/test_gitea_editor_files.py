import base64
import unittest
from unittest.mock import patch

from selfad.gitea import (
    GiteaFileChange,
    change_repository_files,
    get_repository_file_content,
    list_repository_file_entries,
)
from selfad.settings import GiteaSettings


def gitea_settings() -> GiteaSettings:
    return GiteaSettings(
        internal_url="http://gitea.test",
        public_url="http://gitea.test",
        webhook_url="http://selfad.test/hooks/gitea",
        private_token="token",
        verify_tls=False,
    )


class GiteaEditorFileTests(unittest.TestCase):
    @patch("selfad.gitea._request")
    def test_tree_entries_include_blob_metadata(self, request):
        request.return_value = {
            "tree": [
                {"path": "src", "type": "tree", "sha": "tree-sha", "size": 0},
                {"path": "src/app.py", "type": "blob", "sha": "blob-sha", "size": 42},
            ]
        }

        files = list_repository_file_entries(
            gitea_settings(),
            "root/service",
            ref="commit-sha",
        )

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "src/app.py")
        self.assertEqual(files[0].sha, "blob-sha")
        self.assertEqual(files[0].size, 42)

    @patch("selfad.gitea._request")
    def test_file_content_includes_sha(self, request):
        request.return_value = {
            "type": "file",
            "size": 5,
            "sha": "blob-sha",
            "encoding": "base64",
            "content": base64.b64encode(b"hello").decode("ascii"),
        }

        file = get_repository_file_content(
            gitea_settings(),
            "root/service",
            "hello.txt",
            ref="commit-sha",
        )

        self.assertIsNotNone(file)
        self.assertEqual(file.sha, "blob-sha")
        self.assertEqual(file.content, b"hello")

    @patch("selfad.gitea._request")
    def test_multiple_changes_are_sent_as_one_commit(self, request):
        request.return_value = {"commit": {"sha": "new-commit-sha"}}

        commit_sha = change_repository_files(
            gitea_settings(),
            "root/service",
            branch="main",
            message="Submit defense solution",
            author_name="player",
            author_email="player@example.test",
            changes=[
                GiteaFileChange(
                    operation="update",
                    path="app.py",
                    content=b"print('fixed')\n",
                    sha="old-app-sha",
                ),
                GiteaFileChange(
                    operation="delete",
                    path="debug.txt",
                    sha="old-debug-sha",
                ),
            ],
        )

        self.assertEqual(commit_sha, "new-commit-sha")
        self.assertEqual(request.call_count, 1)
        method, path, payload = request.call_args.args[1:]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/repos/root/service/contents")
        self.assertEqual(payload["branch"], "main")
        self.assertEqual(len(payload["files"]), 2)
        self.assertEqual(
            payload["files"][0]["content"],
            base64.b64encode(b"print('fixed')\n").decode("ascii"),
        )
        self.assertNotIn("content", payload["files"][1])


if __name__ == "__main__":
    unittest.main()
