"""Leased ownership and bounded, unpenalized infrastructure retries."""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update

from selfad.database import SessionLocal
from selfad.models import (
    ParticipantRepositoryStatus,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    ServiceRunStatus,
)

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10
LEASE_SECONDS = 120
MAX_CHECK_SECONDS = 1800
MAX_DELIVERIES = 3
RETRY_SECONDS = 30


def owned_events(batch):
    now = datetime.now(timezone.utc)
    return and_(
        RepositoryEvent.id.in_(batch.event_ids),
        RepositoryEvent.status == RepositoryEventStatus.PROCESSING,
        RepositoryEvent.processing_token == batch.processing_token,
        RepositoryEvent.scored_at.is_(None),
        RepositoryEvent.processing_started_at > now - timedelta(seconds=MAX_CHECK_SECONDS),
        RepositoryEvent.heartbeat_at > now - timedelta(seconds=LEASE_SECONDS),
    )


def lock_batch(session, batch, *, scoring=False):
    """Fence stale/duplicate workers in the same transaction as their writes."""
    if not batch.processing_token or not batch.event_ids:
        return False
    values = {"heartbeat_at": datetime.now(timezone.utc)}
    if scoring:
        values["scored_at"] = datetime.now(timezone.utc)
    result = session.execute(update(RepositoryEvent).where(owned_events(batch)).values(**values))
    if result.rowcount != len(batch.event_ids):
        session.rollback()
        return False
    return True


def _requeue(session, events, reason):
    now = datetime.now(timezone.utc)
    for event in events:
        exhausted = event.attempts >= MAX_DELIVERIES
        event.status = RepositoryEventStatus.FAILED if exhausted else RepositoryEventStatus.PENDING
        event.processing_token = None
        event.processing_started_at = None
        event.heartbeat_at = None
        event.retry_after = None if exhausted else now + timedelta(seconds=RETRY_SECONDS)
        event.processed_at = now if exhausted else None
        public_message = (
            f"Infrastructure error; retries exhausted after {event.attempts} deliveries. "
            "No scoring attempt or penalty recorded. "
            if exhausted
            else "Infrastructure error; retry queued without a scoring attempt or penalty. "
        )
        event.message = public_message + reason[:1000]
        player = session.scalar(
            select(ParticipantService)
            .where(
                or_(
                    ParticipantService.attack_repository_path == event.repository_path,
                    ParticipantService.defense_repository_path == event.repository_path,
                )
            )
            .with_for_update()
        )
        if player:
            kind = "attack" if player.attack_repository_path == event.repository_path else "defense"
            setattr(
                player,
                f"{kind}_status",
                ParticipantRepositoryStatus.FAILED
                if exhausted
                else ParticipantRepositoryStatus.RUNNING,
            )
            setattr(player, f"{kind}_message", public_message.strip())
        else:
            service = session.scalar(
                select(Service)
                .where(
                    or_(
                        Service.repository_path == event.repository_path,
                        Service.jury_repository_path == event.repository_path,
                    )
                )
                .with_for_update()
            )
            if service:
                service.runtime_status = (
                    ServiceRunStatus.FAILED if exhausted else ServiceRunStatus.RUNNING
                )
                service.runtime_message = event.message


def retry_batch(batch, reason):
    with SessionLocal() as session:
        if not lock_batch(session, batch):
            return False
        events = session.scalars(
            select(RepositoryEvent).where(
                RepositoryEvent.id.in_(batch.event_ids),
            )
        ).all()
        _requeue(session, events, reason)
        session.commit()
        return True


def recover_stale_batches(*, restart=False):
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        conditions = [
            RepositoryEvent.status == RepositoryEventStatus.PROCESSING,
            RepositoryEvent.scored_at.is_(None),
        ]
        if not restart:
            conditions.append(
                or_(
                    func.coalesce(
                        RepositoryEvent.heartbeat_at, RepositoryEvent.processing_started_at
                    )
                    < now - timedelta(seconds=LEASE_SECONDS),
                    RepositoryEvent.processing_started_at
                    < now - timedelta(seconds=MAX_CHECK_SECONDS),
                    RepositoryEvent.processing_started_at.is_(None),
                )
            )
        # An atomic UPDATE is also a write lock on SQLite, where FOR UPDATE
        # is ignored. Re-evaluate expiry before taking ownership from a worker.
        ids = session.scalars(
            update(RepositoryEvent)
            .where(*conditions)
            .values(
                heartbeat_at=RepositoryEvent.heartbeat_at,
            )
            .returning(RepositoryEvent.id)
        ).all()
        events = session.scalars(
            select(RepositoryEvent)
            .where(
                RepositoryEvent.id.in_(ids),
            )
            .order_by(RepositoryEvent.id)
        ).all()
        _requeue(session, events, "Worker restarted." if restart else "Worker lease expired.")
        session.commit()
        return len(events)


@contextmanager
def maintain_batch_lease(batch):
    stopped = threading.Event()
    started = time.monotonic()

    def heartbeat():
        while not stopped.wait(HEARTBEAT_SECONDS):
            if time.monotonic() - started >= MAX_CHECK_SECONDS:
                return
            try:
                with SessionLocal() as session:
                    if not lock_batch(session, batch):
                        return
                    session.commit()
            except Exception:
                logger.exception("Could not renew repository batch lease.")

    thread = threading.Thread(target=heartbeat, name="selfad-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=1)
