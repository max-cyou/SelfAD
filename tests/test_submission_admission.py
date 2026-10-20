import hashlib
import hmac
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from selfad import worker
from selfad.application import create_app
from selfad.database import get_session
from selfad.gitea import GiteaUnavailable
from selfad.models import (
    InstanceConfig,
    ParticipantService,
    RepositoryEvent,
    ScoringSettings,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    SubmissionAttempt,
)
from selfad.runtime.types import RunnerInfrastructureError, RuntimeCheckResult
from selfad.service_contract import ServiceContractResult
from selfad.workers import participant
from tests import test_worker_recovery as recovery_tests


class SubmissionAdmissionTests(unittest.TestCase):
    fixture_class = recovery_tests.WorkerRecoveryTests

    def setUp(self):
        self.fixture = self.fixture_class("test_runner_outage_retries_same_sha_then_scores_once")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.sessions = self.fixture.sessions
        with self.sessions() as session:
            session.execute(delete(RepositoryEvent))
            service = session.get(Service, 1)
            service.status = ServiceStatus.ACTIVE
            service.runtime_status = ServiceRunStatus.PASSED
            session.commit()
        self.app = create_app()

        def test_session():
            with self.sessions() as session:
                yield session

        self.app.dependency_overrides[get_session] = test_session
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.secret = "s" * 48
        secret = patch("selfad.routes.webhooks.get_gitea_webhook_secret", return_value=self.secret)
        secret.start()
        self.addCleanup(secret.stop)
        head = patch("selfad.routes.webhooks.get_branch_commit", return_value="c" * 40)
        self.head = head.start()
        self.addCleanup(head.stop)

    def push(self, *, sha="c", delivery="first", repository="player/attack"):
        body = json.dumps(
            {"repository": {"full_name": repository}, "ref": "refs/heads/main", "after": sha * 40}
        ).encode()
        return self.client.post(
            "/hooks/gitea",
            content=body,
            headers={
                "x-gitea-event": "push",
                "x-gitea-delivery": delivery,
                "x-gitea-signature": hmac.new(
                    self.secret.encode(), body, hashlib.sha256
                ).hexdigest(),
            },
        )

    def events(self):
        with self.sessions() as session:
            return session.scalars(select(RepositoryEvent).order_by(RepositoryEvent.id)).all()

    def test_duplicates_with_same_or_different_delivery_ids_are_one_attempt(self):
        self.assertEqual(self.push().status_code, 202)
        self.push()
        self.push(delivery="redelivery")
        self.assertEqual(len(self.events()), 1)
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ):
            worker.process_next_repository_batch()
        self.push(delivery="after-completion")
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)

    def test_concurrent_duplicate_webhooks_are_admitted_once(self):
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda id: self.push(delivery=f"delivery-{id}"), range(3)))
        self.assertTrue(all(result.status_code == 202 for result in results))
        self.assertEqual(len(self.events()), 1)

    def test_reverse_delivery_does_not_replace_current_head(self):
        self.push(sha="c")
        response = self.push(sha="b", delivery="delayed-old")
        self.assertIn("Stale push", response.json()["message"])
        self.assertEqual([event.commit_sha for event in self.events()], ["c" * 40])

    def test_queued_pushes_check_latest_admitted_commit(self):
        self.head.return_value = "b" * 40
        self.push(sha="b")
        self.head.return_value = "c" * 40
        self.push(sha="c", delivery="new")
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ) as run:
            worker.process_next_repository_batch()
        self.assertEqual(run.call_args.kwargs["exploit_commit"], "c" * 40)
        with self.sessions() as session:
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)

    def test_new_push_during_check_discards_old_result(self):
        self.push()

        def newer(*args, **kwargs):
            self.head.return_value = "d" * 40
            self.assertEqual(self.push(sha="d", delivery="new").status_code, 202)
            return self.fixture.success

        with patch.object(participant, "run_service_runtime_check", side_effect=newer):
            worker.process_next_repository_batch()
        self.fixture.assert_unscored()
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ) as run:
            worker.process_next_repository_batch()
        self.assertEqual(run.call_args.kwargs["exploit_commit"], "d" * 40)

    def test_admitted_job_finishes_after_end_with_pinned_rules_and_service(self):
        self.push()
        self.assertIsNotNone(self.events()[0].admitted_at)
        with self.sessions() as session:
            session.get(InstanceConfig, 1).contest_ended = True
            service = session.get(Service, 1)
            service.status = ServiceStatus.DRAFT
            service.runtime_source_commit = "e" * 40
            session.get(ScoringSettings, 1).attack_max_points = 100
            session.commit()
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ) as run:
            worker.process_next_repository_batch()
        self.assertEqual(run.call_args.kwargs["contract"].source_commit, "a" * 40)
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_score, 500)

    def test_retry_remains_eligible_after_round_and_contest_close(self):
        self.push()
        with patch.object(
            participant, "run_service_runtime_check", side_effect=RunnerInfrastructureError("down")
        ):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            session.get(InstanceConfig, 1).contest_ended = True
            session.get(Service, 1).status = ServiceStatus.DRAFT
            session.commit()
        self.fixture.due()
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_score, 500)
            self.assertEqual(session.get(ParticipantService, 1).attack_penalty_attempts, 0)

    def test_end_during_execution_still_awards_accepted_result(self):
        self.push()

        def finish(*args, **kwargs):
            with self.sessions() as session:
                session.get(InstanceConfig, 1).contest_ended = True
                session.commit()
            return self.fixture.success

        with patch.object(participant, "run_service_runtime_check", side_effect=finish):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_score, 500)

    def test_closed_services_and_contest_reject_new_admission(self):
        for status in (ServiceStatus.DRAFT, ServiceStatus.READY_TO_ISSUE):
            with self.sessions() as session:
                session.get(Service, 1).status = status
                session.commit()
            self.assertIn("service is closed", self.push().json()["message"])
        with self.sessions() as session:
            session.get(Service, 1).status = ServiceStatus.ACTIVE
            session.get(InstanceConfig, 1).contest_ended = True
            session.commit()
        self.assertIn("contest is not running", self.push().json()["message"])
        self.assertEqual(self.events(), [])

    def test_deadline_is_checked_after_head_verification(self):
        with self.sessions() as session:
            session.get(InstanceConfig, 1).contest_ends_at = datetime.now(timezone.utc) + timedelta(
                milliseconds=20
            )
            session.commit()
        self.head.side_effect = lambda *a, **kw: time.sleep(0.05) or "c" * 40
        self.assertIn("contest is not running", self.push().json()["message"])
        self.assertEqual(self.events(), [])

    def test_gitea_failure_returns_retryable_status_without_admission(self):
        self.head.side_effect = GiteaUnavailable("down")
        self.assertEqual(self.push().status_code, 503)
        self.assertEqual(self.events(), [])

    def test_canonical_webhook_duplicate_does_not_bump_generation(self):
        self.push(repository="root/source")
        self.push(repository="root/source", delivery="duplicate-canonical")
        self.assertEqual(len(self.events()), 1)
        with self.sessions() as session:
            self.assertEqual(session.get(Service, 1).repository_generation, 1)

    def test_runtime_dependencies_are_snapshotted_per_admission(self):
        with self.sessions() as session:
            session.get(ScoringSettings, 1).attack_requirements = "requests==2.32.5"
            session.commit()
        self.push()
        with self.sessions() as session:
            session.get(ScoringSettings, 1).attack_requirements = "urllib3==2.5.0"
            session.commit()
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.fixture.success
        ) as run:
            worker.process_next_repository_batch()
        requirements = run.call_args.kwargs["exploit_runtime_requirements"]
        self.assertIn(b"requests==2.32.5", requirements)
        self.assertNotIn(b"urllib3==2.5.0", requirements)

    def test_defense_admission_is_unlocked_and_pins_jury_and_dockerfile(self):
        response = self.push(repository="player/defense")
        self.assertIn("defense is locked", response.json()["message"])
        self.assertEqual(self.events(), [])
        with self.sessions() as session:
            player = session.get(ParticipantService, 1)
            player.defense_unlocked = True
            player.defense_dockerfile_sha = player.attack_dockerfile_sha
            session.commit()
        self.push(repository="player/defense")
        with self.sessions() as session:
            session.get(InstanceConfig, 1).contest_ended = True
            session.get(Service, 1).runtime_jury_commit = "e" * 40
            session.get(ParticipantService, 1).defense_dockerfile_sha = "f" * 64
            session.commit()
        with (
            patch.object(
                participant,
                "validate_service_contract",
                return_value=ServiceContractResult(True, "ok", "c" * 40, "b" * 40, 8000, "/"),
            ) as contract,
            patch.object(
                participant,
                "run_service_runtime_check",
                return_value=RuntimeCheckResult(False, True, "No flags leaked", 0, "", 10, True),
            ),
        ):
            worker.process_next_repository_batch()
        self.assertEqual(contract.call_args.kwargs["jury_commit"], "b" * 40)
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).defense_score, 100)
