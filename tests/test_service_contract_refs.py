import unittest
from unittest.mock import patch

from selfad.gitea import GiteaRepository
from selfad.service_contract import validate_service_contract
from selfad.settings import GiteaSettings


class ServiceContractRefTests(unittest.TestCase):
    def test_explicit_commits_are_used_instead_of_branch_heads(self):
        settings = GiteaSettings(
            internal_url="http://gitea",
            public_url="http://gitea",
            webhook_url="http://selfad/hooks/gitea",
            private_token="token",
            verify_tls=False,
        )
        source_commit = "a" * 40
        jury_commit = "b" * 40

        def repository_file(_settings, repository_path, file_path, **kwargs):
            expected_ref = (
                source_commit if repository_path == "root/service" else jury_commit
            )
            self.assertEqual(kwargs["ref"], expected_ref)
            return {
                ("root/service", "Dockerfile"): b"FROM alpine:3.22\n",
                ("root/service", "selfad.yml"): (
                    b"version: 1\nservice:\n  port: 8080\n"
                    b"  healthcheck: /health\n"
                ),
                ("root/service-jury", "inject.py"): b"print('inject')\n",
                ("root/service-jury", "exploit.py"): b"print('exploit')\n",
                ("root/service-jury", "checker.py"): None,
                ("root/service-jury", "requirements.txt"): None,
            }[(repository_path, file_path)]

        repositories = {
            "root/service": GiteaRepository(1, "root/service", "main", False),
            "root/service-jury": GiteaRepository(
                2, "root/service-jury", "main", False
            ),
        }
        with (
            patch(
                "selfad.service_contract.get_repository",
                side_effect=lambda _settings, path: repositories[path],
            ),
            patch("selfad.service_contract.get_branch_commit") as branch_commit,
            patch(
                "selfad.service_contract.get_repository_file",
                side_effect=repository_file,
            ),
        ):
            result = validate_service_contract(
                settings,
                repository_path="root/service",
                jury_repository_path="root/service-jury",
                default_branch="main",
                source_commit=source_commit,
                jury_commit=jury_commit,
            )

        self.assertTrue(result.valid, result.message)
        self.assertEqual(result.source_commit, source_commit)
        self.assertEqual(result.jury_commit, jury_commit)
        branch_commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
