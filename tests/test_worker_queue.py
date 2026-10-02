import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import selfad.worker as worker
from selfad.database import Base
from selfad.models import RepositoryEvent, RepositoryEventStatus, Service


class WorkerQueueTests(unittest.TestCase):
    def test_concurrent_workers_claim_one_repository_batch_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "queue.db"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            self.addCleanup(engine.dispose)
            Base.metadata.create_all(engine)
            local_session = sessionmaker(bind=engine, expire_on_commit=False)
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


if __name__ == "__main__":
    unittest.main()
