import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.application import create_app
from selfad.database import Base, get_session
from selfad.models import (
    InstanceConfig,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    ServiceStatus,
    SubmissionAttempt,
    User,
)


class SubmissionsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.now = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
        with Session(self.engine, expire_on_commit=False) as session:
            self.admin = User(id=1, username="admin", email="admin@test.test",
                              password_hash="unused", is_admin=True)
            self.alice = User(id=2, username="alice", email="alice@test.test",
                              password_hash="unused")
            session.add_all([self.admin, self.alice, InstanceConfig(id=1, setup_complete=True),
                             Service(id=1, name="Telemetry", slug="telemetry",
                                     repository_path="root/telemetry", status=ServiceStatus.ACTIVE),
                             Service(id=2, name="Unissued secret service", slug="secret",
                                     repository_path="root/secret")])
            for user in [self.admin, self.alice]:
                session.add(ParticipantService(
                    id=user.id, user_id=user.id, service_id=1,
                    attack_repository_path=f"{user.username}/attack",
                    defense_repository_path=f"{user.username}/defense",
                    attack_dockerfile_sha="a" * 40, defense_dockerfile_sha="b" * 40,
                ))
            session.add_all([
                ParticipantService(id=3, user_id=1, service_id=2,
                                   attack_repository_path="admin/secret-attack",
                                   defense_repository_path="admin/secret-defense",
                                   attack_dockerfile_sha="a" * 40,
                                   defense_dockerfile_sha="b" * 40),
                self.attempt(1, 1, completed=True, improved=True, matched_flags=9,
                             raw_score=450, penalty=5, awarded_score=445),
                self.attempt(2, 2, kind="defense", completed=False,
                             message="PRIVATE_FLAG <script>alert(1)</script>"),
                RepositoryEvent(id=1, delivery_id="queued", repository_path="alice/attack",
                                ref="refs/heads/main", commit_sha="c" * 40,
                                message="PRIVATE_QUEUE_DIAGNOSTIC",
                                received_at=self.now + timedelta(minutes=1)),
                RepositoryEvent(id=2, delivery_id="running", repository_path="admin/defense",
                                ref="refs/heads/main", commit_sha="d" * 40,
                                status=RepositoryEventStatus.PROCESSING,
                                received_at=self.now + timedelta(minutes=2)),
                RepositoryEvent(id=3, delivery_id="other-branch", repository_path="alice/attack",
                                ref="refs/heads/dev", commit_sha="e" * 40),
                RepositoryEvent(id=4, delivery_id="source", repository_path="root/telemetry",
                                ref="refs/heads/main", commit_sha="f" * 40),
            ])
            session.commit()
        self.app = create_app()

        def get_test_session():
            with Session(self.engine) as session:
                yield session

        self.app.dependency_overrides[get_session] = get_test_session
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)
        self.auth = patch("selfad.routes.submissions.get_session_user", return_value=self.admin)
        self.current_user = self.auth.start()
        self.addCleanup(self.auth.stop)

    def attempt(self, id, participant_service_id, **values):
        return SubmissionAttempt(
            id=id, participant_service_id=participant_service_id,
            repository_path=f"user-{participant_service_id}/attack",
            commit_sha=f"{id:040x}", attempt_number=id, injected_flags=10,
            created_at=self.now + timedelta(seconds=id),
            **{"kind": "attack", "message": "Result recorded.", **values},
        )

    def test_admin_sees_all_attempts_and_pending_pushes(self):
        response = self.client.get("/submissions")
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["status"] for row in rows],
                         ["running", "queued", "error", "passed"])
        self.assertEqual({row["username"] for row in rows}, {"admin", "alice"})
        self.assertIn("PRIVATE_FLAG", response.text)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertIn('href="/submissions"', response.text)
        self.assertEqual(rows[-1]["awarded_score"], 445)

    def test_admin_filters_and_invalid_parameters(self):
        response = self.client.get("/submissions?participant=alice&kind=attack&status=queued")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["commit_sha"], "c" * 40)
        response = self.client.get("/submissions?service=999")
        self.assertEqual(response.context["total"], 0)
        self.assertIn("No submissions match", response.text)
        response = self.client.get("/submissions?page=garbage&service=bad&kind=bad&status=bad")
        self.assertEqual(response.context["total"], 4)
        self.assertEqual(response.context["page"], 1)

    def test_pagination_is_bounded_and_preserves_filters(self):
        with Session(self.engine) as session:
            for id in range(3, 28):
                session.add(self.attempt(id, 2, completed=True))
            session.commit()
        response = self.client.get("/submissions?participant=alice&page=1")
        self.assertEqual(len(response.context["rows"]), 20)
        self.assertIn("participant=alice", response.context["next_url"])
        response = self.client.get("/submissions?participant=alice&page=999")
        self.assertEqual(response.context["page"], 2)
        self.assertEqual(len(response.context["rows"]), 7)

    def test_login_required_and_participants_wait_for_start(self):
        self.current_user.return_value = None
        response = self.client.get("/submissions", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/login")
        self.current_user.return_value = self.alice
        response = self.client.get("/submissions", follow_redirects=False)
        self.assertEqual(response.headers["location"], "/")

    def start_contest(self, *, ended=False):
        with Session(self.engine) as session:
            config = session.get(InstanceConfig, 1)
            config.contest_started = True
            config.contest_ended = ended
            session.commit()
        self.current_user.return_value = self.alice

    def test_participant_scope_cannot_be_overridden_and_messages_are_excluded(self):
        self.start_contest()
        response = self.client.get("/submissions?participant=admin&user_id=1")
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["user_id"] for row in rows}, {2})
        self.assertTrue(all(row["message"] == "" for row in rows))
        self.assertNotIn("PRIVATE_FLAG", response.text)
        self.assertNotIn("PRIVATE_QUEUE_DIAGNOSTIC", response.text)
        self.assertNotIn("Unissued secret service", response.text)
        self.assertNotIn('name="participant"', response.text)
        self.assertNotIn("Recorded diagnostics", response.text)
        self.assertIn('href="/submissions"', response.text)
        self.assertEqual([service.id for service in response.context["services"]], [1])
        self.assertEqual(response.context["filters"]["participant"], "")
        response = self.client.get("/submissions?service=2")
        self.assertEqual(response.context["total"], 0)
        self.assertNotIn("Unissued secret service", response.text)

    def test_participants_can_read_history_after_contest_end(self):
        self.start_contest(ended=True)
        response = self.client.get("/submissions")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 2)

    def test_participant_pagination_and_status_filters_are_scoped(self):
        self.start_contest()
        with Session(self.engine) as session:
            for id in range(3, 28):
                session.add(self.attempt(id, 1, completed=True))
            session.commit()
        response = self.client.get("/submissions?page=999&status=queued")
        self.assertEqual(response.context["page"], 1)
        self.assertEqual(response.context["total"], 1)
        self.assertEqual(response.context["rows"][0]["user_id"], 2)

    def test_service_card_links_to_filtered_history(self):
        self.start_contest()
        with patch("selfad.routes.participants.get_session_user", return_value=self.alice):
            response = self.client.get("/services")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/submissions?service=1", response.text)
        self.assertIn("View history", response.text)

    def test_history_does_not_mutate_scores_or_attempts(self):
        self.client.get("/submissions")
        with Session(self.engine) as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_score, 0)
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 2)

    def test_unevaluated_failure_remains_visible_without_private_details(self):
        self.start_contest()
        with Session(self.engine) as session:
            event = session.get(RepositoryEvent, 1)
            event.status = RepositoryEventStatus.FAILED
            event.message = "PRIVATE_RETRY_DIAGNOSTIC"
            session.commit()
        response = self.client.get("/submissions?status=error")
        self.assertEqual(response.context["total"], 2)
        self.assertIn("No scoring attempt or penalty was recorded", response.text)
        self.assertNotIn("PRIVATE_RETRY_DIAGNOSTIC", response.text)
        self.assertEqual(len(response.context["rows"]), 2)


if __name__ == "__main__":
    unittest.main()
