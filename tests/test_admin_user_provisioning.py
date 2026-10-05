import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.database import Base, get_session
from selfad.gitea import GiteaUser
from selfad.models import (
    ParticipantService,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    User,
)
from selfad.routes.admin_users import router
from selfad.settings import GiteaSettings


class AdminUserProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            admin = User(
                username="admin",
                email="admin@example.test",
                password_hash="hash",
                is_admin=True,
            )
            active = Service(
                name="Active service",
                slug="active-service",
                repository_path="root/active-service",
                jury_repository_path="root/active-service-jury",
                status=ServiceStatus.ACTIVE,
                runtime_status=ServiceRunStatus.PASSED,
                runtime_source_commit="a" * 40,
                runtime_jury_commit="b" * 40,
                container_port=8080,
            )
            draft = Service(
                name="Draft service",
                slug="draft-service",
                repository_path="root/draft-service",
                jury_repository_path="root/draft-service-jury",
                status=ServiceStatus.DRAFT,
                runtime_status=ServiceRunStatus.PASSED,
                runtime_source_commit="c" * 40,
                runtime_jury_commit="d" * 40,
                container_port=8080,
            )
            ready = Service(
                name="Ready service",
                slug="ready-service",
                repository_path="root/ready-service",
                jury_repository_path="root/ready-service-jury",
                status=ServiceStatus.READY_TO_ISSUE,
                runtime_status=ServiceRunStatus.PASSED,
                runtime_source_commit="1" * 40,
                runtime_jury_commit="2" * 40,
                container_port=8080,
            )
            session.add_all((admin, active, draft, ready))
            session.commit()
            session.refresh(admin)
            self.admin = admin

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

    def test_created_user_immediately_receives_every_active_service(self):
        provisioned_slugs: list[str] = []

        def provision(_settings, *, service, user, **_kwargs):
            provisioned_slugs.append(service.slug)
            return ParticipantService(
                service_id=service.id,
                user_id=user.id,
                attack_repository_id=101,
                attack_repository_path=f"root/{service.slug}-{user.username}-attack",
                defense_repository_id=102,
                defense_repository_path=f"root/{service.slug}-{user.username}-defense",
                attack_dockerfile_sha="e" * 64,
                defense_dockerfile_sha="f" * 64,
            )

        settings = GiteaSettings(
            internal_url="http://gitea:3000",
            public_url="https://git.example.test",
            webhook_url="http://selfad:8000/hooks/gitea",
            private_token="token",
            verify_tls=False,
        )
        with (
            patch(
                "selfad.routes.admin_users.get_admin_access",
                return_value=(self.admin, None),
            ),
            patch(
                "selfad.routes.admin_users.csrf_token_is_valid",
                return_value=True,
            ),
            patch(
                "selfad.routes.admin_users.get_gitea_settings",
                return_value=settings,
            ),
            patch(
                "selfad.routes.admin_users.gitea_username_exists",
                return_value=False,
            ),
            patch(
                "selfad.routes.admin_users.create_gitea_user",
                return_value=GiteaUser(id=42, username="participant"),
            ),
            patch(
                "selfad.routes.admin_users.add_user_ssh_key",
                return_value=43,
            ),
            patch(
                "selfad.routes.admin_users.get_gitea_webhook_secret",
                return_value="w" * 48,
            ),
            patch(
                "selfad.routes.admin_users.provision_participant_service",
                side_effect=provision,
            ),
        ):
            response = self.client.post(
                "/admin/participants",
                data={
                    "username": "participant",
                    "email": "participant@example.test",
                    "password": "correct-password",
                    "ssh_public_key": "ssh-ed25519 AAAATEST",
                    "role": "user",
                    "csrf_token": "token",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(provisioned_slugs, ["active-service"])
        with Session(self.engine) as session:
            participant = session.scalar(select(User).where(User.username == "participant"))
            self.assertIsNotNone(participant)
            assignments = session.scalars(
                select(ParticipantService).where(ParticipantService.user_id == participant.id)
            ).all()
            self.assertEqual(len(assignments), 1)


if __name__ == "__main__":
    unittest.main()
