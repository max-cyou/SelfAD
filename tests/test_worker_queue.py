import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import selfad.worker as worker
from selfad.database import Base
from selfad.models import (
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
)
from selfad.service_contract import ServiceContractResult


class WorkerQueueTests(unittest.TestCase):
    def _database(self, directory: str):
        database_path = Path(directory) / "queue.db"
        engine = create_engine(
            f"sqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine, expire_on_commit=False)

    def test_concurrent_workers_claim_one_repository_batch_once(self):
        with tempfile.TemporaryDirectory() as directory:
            local_session = self._database(directory)
            with local_session() as session:
                service = Service(
                    name="Queue test",
                    slug="queue-test",
                    repository_path="root/queue-test",
                    jury_repository_path="root/queue-test-jury",
                )
                session.add(service)
                session.flush()
                session.add(
                    RepositoryEvent(
                        delivery_id="queue-test-delivery",
                        repository_path=service.repository_path,
                        ref="refs/heads/main",
                        commit_sha="a" * 40,
                    )
                )
                session.commit()

            with patch.object(worker, "SessionLocal", local_session):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    claims = list(executor.map(lambda _: worker._claim_repository_batch(), range(2)))
                claimed = [batch for batch in claims if batch is not None]
                self.assertEqual(len(claimed), 1)
                batch = claimed[0]
                with local_session() as session:
                    event = session.query(RepositoryEvent).one()
                    self.assertEqual(event.status, RepositoryEventStatus.PROCESSING)
                    self.assertTrue(event.processing_token)
                    worker._finish_events(
                        session,
                        batch.event_ids,
                        RepositoryEventStatus.DONE,
                        "queue test complete",
                    )
                    session.commit()
                with local_session() as session:
                    event = session.query(RepositoryEvent).one()
                    self.assertEqual(event.status, RepositoryEventStatus.DONE)
                    self.assertIsNone(event.processing_token)

    def test_service_batch_keeps_latest_submitted_sha_for_each_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            local_session = self._database(directory)
            with local_session() as session:
                service = Service(
                    name="SHA test",
                    slug="sha-test",
                    repository_path="root/sha-test",
                    jury_repository_path="root/sha-test-jury",
                    repository_generation=3,
                )
                session.add(service)
                session.flush()
                session.add_all(
                    [
                        RepositoryEvent(
                            delivery_id="source-old",
                            repository_path=service.repository_path,
                            ref="refs/heads/main",
                            commit_sha="a" * 40,
                        ),
                        RepositoryEvent(
                            delivery_id="jury",
                            repository_path=service.jury_repository_path,
                            ref="refs/heads/main",
                            commit_sha="b" * 40,
                        ),
                        RepositoryEvent(
                            delivery_id="source-new",
                            repository_path=service.repository_path,
                            ref="refs/heads/main",
                            commit_sha="c" * 40,
                        ),
                    ]
                )
                session.commit()

            with patch.object(worker, "SessionLocal", local_session):
                batch = worker._claim_repository_batch()

            self.assertIsNotNone(batch)
            assert batch is not None
            self.assertEqual(batch.source_commit, "c" * 40)
            self.assertEqual(batch.jury_commit, "b" * 40)
            self.assertEqual(batch.repository_generation, 3)
            self.assertEqual(len(batch.event_ids), 3)

    def test_service_worker_validates_the_commits_captured_by_the_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            local_session = self._database(directory)
            with local_session() as session:
                service = Service(
                    name="Pinned refs",
                    slug="pinned-refs",
                    repository_path="root/pinned-refs",
                    jury_repository_path="root/pinned-refs-jury",
                    repository_generation=2,
                )
                session.add(service)
                session.flush()
                event = RepositoryEvent(
                    delivery_id="pinned-refs",
                    repository_path=service.repository_path,
                    ref="refs/heads/main",
                    commit_sha="a" * 40,
                    status=RepositoryEventStatus.PROCESSING,
                    processing_token="worker",
                )
                session.add(event)
                session.flush()
                batch = worker.RepositoryBatch(
                    event_ids=(event.id,),
                    service_id=service.id,
                    repository_generation=service.repository_generation,
                    source_commit="a" * 40,
                    jury_commit="b" * 40,
                )
                session.commit()

            invalid = ServiceContractResult(False, "invalid test contract")
            with (
                patch.object(worker, "SessionLocal", local_session),
                patch.object(worker, "_claim_repository_batch", return_value=batch),
                patch.object(worker, "get_gitea_settings", return_value=object()),
                patch.object(
                    worker,
                    "validate_service_contract",
                    return_value=invalid,
                ) as validate,
                patch.object(worker, "get_branch_commit") as branch_commit,
            ):
                self.assertTrue(worker.process_next_repository_batch())

            branch_commit.assert_not_called()
            self.assertEqual(validate.call_args.kwargs["source_commit"], "a" * 40)
            self.assertEqual(validate.call_args.kwargs["jury_commit"], "b" * 40)

    def test_participant_repository_is_not_claimed_twice_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            local_session = self._database(directory)
            with local_session() as session:
                service = Service(
                    name="Participant test",
                    slug="participant-test",
                    repository_path="root/participant-test",
                    jury_repository_path="root/participant-test-jury",
                )
                session.add(service)
                session.flush()
                player = ParticipantService(
                    service_id=service.id,
                    user_id=1,
                    attack_repository_path="player/participant-test-attack",
                    defense_repository_path="player/participant-test-defense",
                    attack_dockerfile_sha="d" * 64,
                    defense_dockerfile_sha="e" * 64,
                )
                session.add(player)
                session.add_all(
                    [
                        RepositoryEvent(
                            delivery_id="in-flight",
                            repository_path=player.attack_repository_path,
                            ref="refs/heads/main",
                            commit_sha="a" * 40,
                            status=RepositoryEventStatus.PROCESSING,
                            processing_token="worker-one",
                        ),
                        RepositoryEvent(
                            delivery_id="queued",
                            repository_path=player.attack_repository_path,
                            ref="refs/heads/main",
                            commit_sha="b" * 40,
                        ),
                    ]
                )
                session.commit()

            with patch.object(worker, "SessionLocal", local_session):
                self.assertIsNone(worker._claim_repository_batch())

            with local_session() as session:
                queued = session.query(RepositoryEvent).filter_by(
                    delivery_id="queued"
                ).one()
                self.assertEqual(queued.status, RepositoryEventStatus.PENDING)

    def test_newer_event_supersedes_an_in_flight_participant_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            local_session = self._database(directory)
            with local_session() as session:
                old = RepositoryEvent(
                    delivery_id="old",
                    repository_path="player/service-attack",
                    ref="refs/heads/main",
                    commit_sha="a" * 40,
                    status=RepositoryEventStatus.PROCESSING,
                    processing_token="old-worker",
                )
                session.add(old)
                session.flush()
                batch = worker.RepositoryBatch(
                    event_ids=(old.id,),
                    service_id=1,
                    repository_generation=0,
                    participant_service_id=1,
                    repository_path=old.repository_path,
                    commit_sha=old.commit_sha,
                )
                self.assertFalse(worker._batch_is_superseded(session, batch))
                session.add(
                    RepositoryEvent(
                        delivery_id="new",
                        repository_path=old.repository_path,
                        ref="refs/heads/main",
                        commit_sha="b" * 40,
                    )
                )
                session.flush()
                self.assertTrue(worker._batch_is_superseded(session, batch))


if __name__ == "__main__":
    unittest.main()
