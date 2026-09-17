import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from selfad.gitea import create_repository, ensure_ssh_key
from selfad.settings import GiteaSettings, set_gitea_root_password


class GiteaServiceAccountTests(unittest.TestCase):
    def setUp(self):
        self.settings = GiteaSettings(
            internal_url="http://gitea.test",
            public_url="https://git.example.test",
            webhook_url="http://selfad.test/hooks/gitea",
            private_token="token",
            verify_tls=False,
        )

    @patch("selfad.gitea._request")
    def test_service_repositories_remain_owned_by_root(self, request):
        request.return_value = {
            "id": 7,
            "full_name": "root/example",
            "default_branch": "main",
            "empty": True,
        }

        repository = create_repository(
            self.settings,
            path="example",
            description="Example",
            default_branch="main",
        )

        self.assertEqual(repository.path, "root/example")
        self.assertEqual(request.call_args.args[2], "/admin/users/root/repos")

    @patch("selfad.gitea._request")
    def test_organizer_ssh_key_is_attached_to_root(self, request):
        request.side_effect = [[], {"id": 9}]

        key_id = ensure_ssh_key(self.settings, "ssh-ed25519 AAAA organizer")

        self.assertEqual(key_id, 9)
        self.assertEqual(request.call_args_list[0].args[2], "/users/root/keys?limit=100")
        self.assertEqual(request.call_args_list[1].args[2], "/admin/users/root/keys")

    def test_root_password_file_is_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            password_file = Path(directory) / "gitea_admin_password"
            password_file.write_text("old-password", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"SELFAD_GITEA_PASSWORD_FILE": str(password_file)},
            ):
                set_gitea_root_password("new-password")

            self.assertEqual(
                password_file.read_text(encoding="utf-8"),
                "new-password",
            )
            self.assertEqual(
                stat.S_IMODE(password_file.stat().st_mode),
                0o640,
            )


if __name__ == "__main__":
    unittest.main()
