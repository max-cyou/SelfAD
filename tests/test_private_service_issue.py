import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.database import Base, get_session
from selfad.gitea import GiteaRepository
from selfad.models import ParticipantService, Service, ServiceRunStatus, ServiceStatus, User
from selfad.participants import (
    grant_participant_service_access,
    provision_participant_service,
    revoke_participant_service_access,
)
from selfad.settings import GiteaSettings


class PrivateServiceIssueTests(unittest.TestCase):
    def setUp(self):
        self.settings = GiteaSettings(
            internal_url="http://gitea:3000",
            public_url="https://git.example.test",
            webhook_url="http://selfad:8000/hooks/gitea",
            private_token="token",
            verify_tls=False,
        )
        self.service = SimpleNamespace(
            id=7,
            name="Hidden service",
            slug="hidden-service",
            default_branch="main",
            repository_path="root/hidden-service",
            jury_repository_path="root/hidden-service-jury",
            runtime_source_commit="a" * 40,
            container_port=8080,
        )
        self.user = SimpleNamespace(
            id=9,
            username="participant",
            gitea_username="participant",
        )

    @patch("selfad.participants.add_repository_collaborator")
    @patch("selfad.participants.ensure_repository_webhook")
    @patch("selfad.participants.get_repository_file", return_value=b"FROM scratch\n")
    @patch("selfad.participants.list_repository_files", return_value=["Dockerfile"])
    @patch("selfad.participants.create_repository_file")
    @patch("selfad.participants.create_repository")
    def test_private_issue_creates_repositories_without_granting_access(
        self,
        create_repository,
        _create_repository_file,
        _list_repository_files,
        _get_repository_file,
        _ensure_repository_webhook,
        add_repository_collaborator,
    ):
        create_repository.side_effect = [
            GiteaRepository(11, "root/hidden-service-participant-attack", "main", True),
            GiteaRepository(12, "root/hidden-service-participant-defense", "main", True),
        ]

        assignment = provision_participant_service(
            self.settings,
            service=self.service,
            user=self.user,
            attack_requirements="",
            allow_user_attack_requirements=False,
            webhook_secret="w" * 48,
            grant_access=False,
        )

        self.assertEqual(assignment.user_id, self.user.id)
        add_repository_collaborator.assert_not_called()

    @patch("selfad.participants.add_repository_collaborator")
    def test_activation_grants_all_three_repository_permissions(self, add_collaborator):
        assignment = SimpleNamespace(
            attack_repository_path="root/hidden-service-participant-attack",
            defense_repository_path="root/hidden-service-participant-defense",
        )

        grant_participant_service_access(
            self.settings,
            service=self.service,
            user=self.user,
            assignment=assignment,
        )

        self.assertEqual(
            add_collaborator.call_args_list,
            [
                call(
                    self.settings,
                    "root/hidden-service",
                    username="participant",
                    permission="read",
                ),
                call(
                    self.settings,
                    "root/hidden-service-participant-attack",
                    username="participant",
                    permission="write",
                ),
                call(
                    self.settings,
                    "root/hidden-service-participant-defense",
                    username="participant",
                    permission="write",
                ),
            ],
        )

    @patch("selfad.participants.remove_repository_collaborator")
    def test_leaving_active_revokes_all_repository_permissions(self, remove_collaborator):
        assignment = SimpleNamespace(
            attack_repository_path="root/hidden-service-participant-attack",
            defense_repository_path="root/hidden-service-participant-defense",
        )

        revoke_participant_service_access(
            self.settings,
            service=self.service,
            user=self.user,
            assignment=assignment,
        )

        self.assertEqual(remove_collaborator.call_count, 3)


class PrivateIssueRouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            self.admin = User(
                username="admin",
                email="admin@example.test",
                password_hash="hash",
                is_admin=True,
            )
            participant = User(
                username="participant",
                email="participant@example.test",
                password_hash="hash",
                gitea_user_id=10,
                gitea_username="participant",
            )
            service = Service(
                name="Hidden service",
                slug="hidden-service",
                repository_path="root/hidden-service",
                jury_repository_path="root/hidden-service-jury",
                status=ServiceStatus.READY_TO_ISSUE,
                runtime_status=ServiceRunStatus.PASSED,
                runtime_source_commit="a" * 40,
                runtime_jury_commit="b" * 40,
                container_port=8080,
            )
            session.add_all((self.admin, participant, service))
            session.commit()
            self.service_id = service.id

        from selfad.routes.admin_services import router

        app = FastAPI()
        app.include_router(router)

        def test_session():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_issue_route_keeps_service_ready_and_withholds_access(self):
        grant_access_values = []

        def provision(_settings, *, service, user, grant_access, **_kwargs):
            grant_access_values.append(grant_access)
            return ParticipantService(
                service_id=service.id,
                user_id=user.id,
                attack_repository_id=101,
                attack_repository_path="root/hidden-service-participant-attack",
                defense_repository_id=102,
                defense_repository_path="root/hidden-service-participant-defense",
                attack_dockerfile_sha="c" * 64,
                defense_dockerfile_sha="d" * 64,
            )

        with (
            patch(
                "selfad.routes.admin_services.get_admin_access",
                return_value=(self.admin, None),
            ),
            patch(
                "selfad.routes.admin_services.csrf_token_is_valid",
                return_value=True,
            ),
            patch(
                "selfad.routes.admin_services.provision_participant_service",
                side_effect=provision,
            ),
        ):
            response = self.client.post(
                f"/admin/services/{self.service_id}/issue",
                data={"csrf_token": "token"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(grant_access_values, [False])
        with Session(self.engine) as session:
            service = session.get(Service, self.service_id)
            self.assertEqual(service.status, ServiceStatus.READY_TO_ISSUE)
            assignments = session.scalars(
                select(ParticipantService).where(
                    ParticipantService.service_id == self.service_id
                )
            ).all()
            self.assertEqual(len(assignments), 1)


if __name__ == "__main__":
    unittest.main()
