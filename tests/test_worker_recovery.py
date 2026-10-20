import hashlib
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.orm import sessionmaker

from selfad import worker
from selfad.database import Base
from selfad.gitea import GiteaUnavailable
from selfad.models import (
    InstanceConfig,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    ScoringSettings,
    Service,
    SubmissionAttempt,
)
from selfad.runtime import docker
from selfad.runtime.types import (
    CommandResult,
    RunnerError,
    RunnerInfrastructureError,
    RuntimeCheckResult,
)
from selfad.scoring import record_submission_score
from selfad.workers import participant, queue, recovery


class WorkerRecoveryTests(unittest.TestCase):
    def make_engine(self, directory):
        return create_engine(
            f"sqlite:///{Path(directory) / 'recovery.db'}",
            connect_args={"check_same_thread": False},
        )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = self.make_engine(directory.name)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        log = patch.object(worker, "logger")
        log.start()
        self.addCleanup(log.stop)
        for module in (worker, queue, recovery, participant):
            mocked = patch.object(module, "SessionLocal", self.sessions)
            mocked.start()
            self.addCleanup(mocked.stop)
        for target, value in (
            ("selfad.workers.participant.get_gitea_settings", object()),
            ("selfad.gitea.get_repository_file", None),
        ):
            mocked = patch(target, return_value=value)
            result = mocked.start()
            if target.endswith("get_repository_file"):
                result.side_effect = lambda _settings, _repo, filename, **kw: {
                    "Dockerfile": b"FROM scratch",
                    "exploit.py": b"print('test')",
                }.get(filename)
            self.addCleanup(mocked.stop)
        with self.sessions() as session:
            session.add_all(
                [
                    InstanceConfig(id=1, setup_complete=True, contest_started=True),
                    ScoringSettings(id=1, attack_max_points=500, penalize_check_errors=True),
                    Service(
                        id=1,
                        name="Recovery",
                        slug="recovery",
                        repository_path="root/source",
                        jury_repository_path="root/jury",
                        runtime_source_commit="a" * 40,
                        runtime_jury_commit="b" * 40,
                        container_port=8000,
                        healthcheck_path="/",
                    ),
                    ParticipantService(
                        id=1,
                        service_id=1,
                        user_id=1,
                        attack_repository_path="player/attack",
                        defense_repository_path="player/defense",
                        attack_dockerfile_sha=hashlib.sha256(b"FROM scratch").hexdigest(),
                        defense_dockerfile_sha="d" * 64,
                    ),
                    RepositoryEvent(
                        id=1,
                        delivery_id="test",
                        repository_path="player/attack",
                        ref="refs/heads/main",
                        commit_sha="c" * 40,
                    ),
                ]
            )
            session.commit()
        self.success = RuntimeCheckResult(True, True, "Success", 10, "", 10, True)

    def due(self):
        with self.sessions() as session:
            session.execute(update(RepositoryEvent).values(retry_after=None))
            session.commit()

    def expire(self, *, hard=False):
        with self.sessions() as session:
            event = session.get(RepositoryEvent, 1)
            if hard:
                event.processing_started_at = datetime.now(timezone.utc) - timedelta(seconds=1900)
            else:
                event.heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=130)
            session.commit()

    def assert_unscored(self):
        with self.sessions() as session:
            player = session.get(ParticipantService, 1)
            self.assertEqual(player.attack_score, 0)
            self.assertEqual(player.attack_attempt_count, 0)
            self.assertEqual(player.attack_penalty_attempts, 0)
            self.assertEqual(session.scalars(select(SubmissionAttempt)).all(), [])

    def test_runner_outage_retries_same_sha_then_scores_once(self):
        with patch.object(
            participant,
            "run_service_runtime_check",
            side_effect=RunnerInfrastructureError("runner down"),
        ):
            worker.process_next_repository_batch()
        self.assert_unscored()
        self.assertIsNone(queue._claim_repository_batch())  # backoff enforced
        with self.sessions() as session:
            event = session.get(RepositoryEvent, 1)
            self.assertEqual(event.status, RepositoryEventStatus.PENDING)
            self.assertEqual(event.commit_sha, "c" * 40)
            self.assertIsNotNone(event.retry_after)
        self.due()
        batch = queue._claim_repository_batch()
        with patch.object(participant, "run_service_runtime_check", return_value=self.success):
            participant._process_participant_batch(batch)
            participant._process_participant_batch(batch)  # duplicate delivery
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_score, 500)
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)
            self.assertIsNotNone(session.get(RepositoryEvent, 1).scored_at)
        self.assertEqual(queue.recover_interrupted_repository_events(), 0)
        self.assertIsNone(queue._claim_repository_batch())

    def test_retry_budget_exhaustion_does_not_penalize(self):
        with patch.object(
            participant,
            "run_service_runtime_check",
            side_effect=RunnerInfrastructureError("private transport detail"),
        ):
            for _ in range(3):
                self.due()
                worker.process_next_repository_batch()
        self.assert_unscored()
        with self.sessions() as session:
            event = session.get(RepositoryEvent, 1)
            self.assertEqual(event.status, RepositoryEventStatus.FAILED)
            self.assertEqual(event.attempts, 3)
            self.assertIn("retries exhausted", event.message)
            self.assertNotIn(
                "private transport detail", session.get(ParticipantService, 1).attack_message
            )
        self.assertIsNone(queue._claim_repository_batch())

    def test_expired_worker_cannot_publish_after_reclaim(self):
        old = queue._claim_repository_batch()
        self.expire()
        self.assertEqual(recovery.recover_stale_batches(), 1)
        self.due()
        new = queue._claim_repository_batch()
        self.assertNotEqual(old.processing_token, new.processing_token)
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.success
        ) as run:
            participant._process_participant_batch(old)
            run.assert_not_called()
            participant._process_participant_batch(new)
        with self.sessions() as session:
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)

    def test_lease_expires_during_execution_and_rejects_late_result(self):
        batch = queue._claim_repository_batch()

        def late_result(*args, **kwargs):
            self.expire()
            recovery.recover_stale_batches()
            return self.success

        with patch.object(participant, "run_service_runtime_check", side_effect=late_result):
            participant._process_participant_batch(batch)
        self.assert_unscored()
        with self.sessions() as session:
            self.assertEqual(session.get(RepositoryEvent, 1).status, RepositoryEventStatus.PENDING)

    def test_restart_requeues_only_uncommitted_work(self):
        batch = queue._claim_repository_batch()
        self.assertEqual(recovery.recover_stale_batches(), 0)  # live lease
        self.assertEqual(queue.recover_interrupted_repository_events(), 1)
        with self.sessions() as session:
            self.assertFalse(recovery.lock_batch(session, batch, scoring=True))
        self.assert_unscored()

    def test_crash_after_score_flush_rolls_back_everything(self):
        def crash(*args, **kwargs):
            record_submission_score(*args, **kwargs)
            raise RuntimeError("simulated crash before commit")

        with (
            patch.object(participant, "run_service_runtime_check", return_value=self.success),
            patch.object(participant, "record_submission_score", side_effect=crash),
        ):
            worker.process_next_repository_batch()
        self.assert_unscored()
        with self.sessions() as session:
            self.assertIsNone(session.get(RepositoryEvent, 1).scored_at)
        self.due()
        with patch.object(participant, "run_service_runtime_check", return_value=self.success):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)

    def test_exploit_error_remains_a_scored_failure(self):
        result = RuntimeCheckResult(False, True, "Exploit exit 1", 0, "")
        with patch.object(participant, "run_service_runtime_check", return_value=result):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            self.assertEqual(session.get(RepositoryEvent, 1).status, RepositoryEventStatus.DONE)
            self.assertEqual(session.get(ParticipantService, 1).attack_penalty_attempts, 1)
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)

    def test_durable_fence_survives_attempt_history_pruning(self):
        batch = queue._claim_repository_batch()
        with patch.object(participant, "run_service_runtime_check", return_value=self.success):
            participant._process_participant_batch(batch)
        with self.sessions() as session:
            session.execute(delete(SubmissionAttempt))
            # Even an accidental requeue of an already-scored event must not
            # create a second scoring attempt once the audit tail was pruned.
            session.execute(
                update(RepositoryEvent).values(
                    status=RepositoryEventStatus.PROCESSING,
                    processing_token=batch.processing_token,
                    processing_started_at=datetime.now(timezone.utc),
                    heartbeat_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        with self.sessions() as session:
            self.assertFalse(recovery.lock_batch(session, batch, scoring=True))
        with patch.object(
            participant, "run_service_runtime_check", return_value=self.success
        ) as run:
            participant._process_participant_batch(batch)
            run.assert_not_called()
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)
            self.assertEqual(session.scalars(select(SubmissionAttempt)).all(), [])

    def test_failure_after_successful_commit_cannot_requeue_or_rescore(self):
        original = worker._process_participant_batch

        def fail_after_commit(batch):
            original(batch)
            raise RuntimeError("connection lost after commit acknowledgement")

        with (
            patch.object(participant, "run_service_runtime_check", return_value=self.success),
            patch.object(worker, "_process_participant_batch", side_effect=fail_after_commit),
        ):
            worker.process_next_repository_batch()
        with self.sessions() as session:
            self.assertEqual(session.get(RepositoryEvent, 1).status, RepositoryEventStatus.DONE)
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)
        self.assertEqual(queue.recover_interrupted_repository_events(), 0)
        self.assertIsNone(queue._claim_repository_batch())

    def test_unavailable_gitea_does_not_become_a_scoring_failure(self):
        with patch("selfad.gitea.get_repository_file", side_effect=GiteaUnavailable("down")):
            worker.process_next_repository_batch()
        self.assert_unscored()

    def test_hard_deadline_overrides_healthy_heartbeat(self):
        batch = queue._claim_repository_batch()
        self.expire(hard=True)
        self.assertEqual(recovery.recover_stale_batches(), 1)
        with self.sessions() as session:
            self.assertFalse(recovery.lock_batch(session, batch, scoring=True))

    def test_heartbeat_keeps_long_checks_alive(self):
        batch = queue._claim_repository_batch()
        self.expire()
        with patch.object(recovery, "HEARTBEAT_SECONDS", 0.01):
            # First renew from a valid lease, then observe background renewals.
            with self.sessions() as session:
                session.execute(
                    update(RepositoryEvent).values(heartbeat_at=datetime.now(timezone.utc))
                )
                session.commit()
            with recovery.maintain_batch_lease(batch):
                time.sleep(0.05)
                self.assertEqual(recovery.recover_stale_batches(), 0)

    def test_concurrent_duplicate_completion_is_fenced(self):
        batch = queue._claim_repository_batch()
        with patch.object(participant, "run_service_runtime_check", return_value=self.success):
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(participant._process_participant_batch, [batch, batch]))
        with self.sessions() as session:
            self.assertEqual(session.get(ParticipantService, 1).attack_attempt_count, 1)
            self.assertEqual(len(session.scalars(select(SubmissionAttempt)).all()), 1)


class RunnerInfrastructureClassificationTests(unittest.TestCase):
    def test_dead_daemon_is_infrastructure_error(self):
        with patch.object(
            docker,
            "_run_command",
            side_effect=[
                CommandResult(125, "", "connection lost"),
                CommandResult(1, "", "down"),
            ],
        ):
            with self.assertRaises(RunnerInfrastructureError):
                docker._docker(["run", "test"], timeout=1)

    def test_script_output_cannot_spoof_an_infrastructure_failure(self):
        failure = CommandResult(1, "", "Cannot connect to the Docker daemon")
        with patch.object(docker, "_run_command", side_effect=[failure, CommandResult(0, "29")]):
            self.assertEqual(docker._docker(["run", "test"], timeout=1), failure)

    def test_script_timeout_with_healthy_daemon_is_not_retried(self):
        with patch.object(
            docker, "_run_command", side_effect=[RunnerError("timeout"), CommandResult(0, "29")]
        ):
            with self.assertRaises(RunnerError) as caught:
                docker._docker(["run", "test"], timeout=1)
            self.assertNotIsInstance(caught.exception, RunnerInfrastructureError)


if __name__ == "__main__":
    unittest.main()
