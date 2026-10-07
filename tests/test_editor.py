import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.application import create_app
from selfad.database import Base, get_session
from selfad.editor import (
    EditorSubmittedChange,
    EditorValidationError,
    EditorWorkspace,
    normalize_editor_path,
    prepare_editor_changes,
)
from selfad.gitea import GiteaRepositoryFile
from selfad.models import ParticipantService, Service, ServiceStatus, User


class EditorValidationTests(unittest.TestCase):
    def setUp(self):
        self.workspace = EditorWorkspace(
            kind="defense",
            repository_path="root/service-player-defense",
            ref="a" * 40,
            branch="main",
            editable=True,
        )

    def test_path_traversal_and_git_metadata_are_rejected(self):
        for path in ("../secret", "/etc/passwd", "src\\app.py", ".git/config"):
            with self.subTest(path=path), self.assertRaises(EditorValidationError):
                normalize_editor_path(path)

    def test_dockerfile_is_read_only(self):
        with self.assertRaisesRegex(EditorValidationError, "read-only"):
            prepare_editor_changes(
                self.workspace,
                [
                    EditorSubmittedChange(
                        operation="update",
                        path="Dockerfile",
                        content="FROM busybox\n",
                        sha="b" * 40,
                    )
                ],
            )

    def test_changes_are_converted_to_bytes(self):
        changes = prepare_editor_changes(
            self.workspace,
            [
                EditorSubmittedChange(
                    operation="update",
                    path="src/app.py",
                    content="print('fixed')\n",
                    sha="b" * 40,
                )
            ],
        )

        self.assertEqual(changes[0].content, b"print('fixed')\n")
        self.assertEqual(changes[0].sha, "b" * 40)

    def test_disabled_attack_requirements_are_read_only(self):
        attack = EditorWorkspace(
            kind="attack",
            repository_path="root/service-player-attack",
            ref="a" * 40,
            branch="main",
            editable=True,
            allow_attack_requirements=False,
        )
        with self.assertRaisesRegex(EditorValidationError, "read-only"):
            prepare_editor_changes(
                attack,
                [
                    EditorSubmittedChange(
                        operation="create",
                        path="requirements.txt",
                        content="requests==2.32.5\n",
                        sha=None,
                    )
                ],
            )


class EditorEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            user = User(
                username="player",
                email="player@example.test",
                password_hash="hash",
                gitea_username="player",
            )
            other = User(
                username="other",
                email="other@example.test",
                password_hash="hash",
                gitea_username="other",
            )
            service = Service(
                name="Notes",
                slug="notes",
                repository_path="root/notes",
                default_branch="main",
                status=ServiceStatus.ACTIVE,
                runtime_source_commit="1" * 40,
            )
            session.add_all([user, other, service])
            session.flush()
            assignment = ParticipantService(
                service_id=service.id,
                user_id=user.id,
                attack_repository_path="root/notes-player-attack",
                defense_repository_path="root/notes-player-defense",
                attack_dockerfile_sha="2" * 64,
                defense_dockerfile_sha="3" * 64,
                defense_unlocked=False,
            )
            session.add(assignment)
            session.commit()
            self.user_id = user.id
            self.other_user_id = other.id
            self.assignment_id = assignment.id

        app = create_app()

        def test_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def user(self, user_id: int | None = None) -> User:
        with Session(self.engine) as session:
            return session.get(User, user_id or self.user_id)

    @patch("selfad.routes.editor.list_repository_file_entries")
    @patch("selfad.routes.editor.get_branch_commit", return_value="a" * 40)
    @patch("selfad.routes.editor.participant_access")
    def test_tree_only_exposes_the_users_assignment(self, access, _head, list_files):
        access.return_value = (self.user(), None)
        list_files.return_value = [
            GiteaRepositoryFile(path="Dockerfile", sha="b" * 40, size=20),
            GiteaRepositoryFile(path="exploit.py", sha="c" * 40, size=120),
        ]

        response = self.client.get(
            f"/api/editor/{self.assignment_id}/attack/tree"
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["files"][0]["editable"])
        self.assertTrue(response.json()["files"][1]["editable"])

        access.return_value = (self.user(self.other_user_id), None)
        denied = self.client.get(f"/api/editor/{self.assignment_id}/attack/tree")
        self.assertEqual(denied.status_code, 404)

    @patch("selfad.routes.editor.participant_access")
    def test_locked_defense_is_rejected(self, access):
        access.return_value = (self.user(), None)

        response = self.client.get(
            f"/api/editor/{self.assignment_id}/defense/tree"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Defense is still locked.")

    @patch("selfad.routes.editor.add_user_ssh_key", return_value=71)
    @patch("selfad.routes.editor.csrf_token_is_valid", return_value=True)
    @patch("selfad.routes.editor.participant_access")
    def test_editor_can_add_a_participant_ssh_key(self, access, _csrf, add_key):
        access.return_value = (self.user(), None)

        response = self.client.post(
            "/api/editor/ssh-key",
            headers={"X-CSRF-Token": "token"},
            json={"public_key": "ssh-ed25519 AAAATEST participant@example.test"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "SSH key added."})
        self.assertEqual(add_key.call_args.kwargs["username"], "player")
        with Session(self.engine) as session:
            updated = session.get(User, self.user_id)
            self.assertEqual(updated.git_ssh_key_id, 71)
            self.assertTrue(updated.ssh_public_key.startswith("ssh-ed25519"))

    @patch("selfad.routes.editor.delete_user_ssh_key")
    @patch("selfad.routes.editor.csrf_token_is_valid", return_value=True)
    @patch("selfad.routes.editor.participant_access")
    def test_editor_can_remove_a_participant_ssh_key(self, access, _csrf, delete_key):
        with Session(self.engine) as session:
            user = session.get(User, self.user_id)
            user.ssh_public_key = "ssh-ed25519 AAAATEST participant@example.test"
            user.git_ssh_key_id = 71
            session.commit()
        access.return_value = (self.user(), None)

        response = self.client.delete(
            "/api/editor/ssh-key",
            headers={"X-CSRF-Token": "token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "SSH key removed."})
        self.assertEqual(delete_key.call_args.kwargs["username"], "player")
        self.assertEqual(delete_key.call_args.kwargs["key_id"], 71)
        with Session(self.engine) as session:
            updated = session.get(User, self.user_id)
            self.assertIsNone(updated.git_ssh_key_id)
            self.assertIsNone(updated.ssh_public_key)

    @patch("selfad.routes.editor.change_repository_files", return_value="d" * 40)
    @patch("selfad.routes.editor.csrf_token_is_valid", return_value=True)
    @patch("selfad.routes.editor.get_branch_commit", return_value="a" * 40)
    @patch("selfad.routes.editor.participant_access")
    def test_submit_creates_one_atomic_commit(
        self,
        access,
        _head,
        _csrf,
        change_files,
    ):
        access.return_value = (self.user(), None)

        response = self.client.post(
            f"/api/editor/{self.assignment_id}/attack/submit",
            headers={"X-CSRF-Token": "token"},
            json={
                "base_commit": "a" * 40,
                "changes": [
                    {
                        "operation": "update",
                        "path": "exploit.py",
                        "content": "print('FLAG')\n",
                        "sha": "b" * 40,
                    },
                    {
                        "operation": "create",
                        "path": "helper.py",
                        "content": "VALUE = 1\n",
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["commit"], "d" * 40)
        self.assertEqual(change_files.call_count, 1)
        changes = change_files.call_args.kwargs["changes"]
        self.assertEqual([change.path for change in changes], ["exploit.py", "helper.py"])
        self.assertEqual(change_files.call_args.kwargs["author_email"], "player@example.test")

    @patch("selfad.routes.editor.change_repository_files")
    @patch("selfad.routes.editor.csrf_token_is_valid", return_value=True)
    @patch("selfad.routes.editor.get_branch_commit", return_value="a" * 40)
    @patch("selfad.routes.editor.participant_access")
    def test_stale_submission_is_rejected_before_commit(
        self,
        access,
        _head,
        _csrf,
        change_files,
    ):
        access.return_value = (self.user(), None)

        response = self.client.post(
            f"/api/editor/{self.assignment_id}/attack/submit",
            headers={"X-CSRF-Token": "token"},
            json={"base_commit": "f" * 40, "changes": []},
        )

        self.assertEqual(response.status_code, 409)
        change_files.assert_not_called()


if __name__ == "__main__":
    unittest.main()
