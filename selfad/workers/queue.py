import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select, update

from selfad.database import SessionLocal
from selfad.models import (
    ParticipantRepositoryStatus,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
)
from selfad.workers.types import (
    RepositoryBatch,
)


def recover_interrupted_repository_events() -> int:
    """Return jobs left in-flight by a stopped control-plane process to queue."""
    with SessionLocal() as session:
        recovered = session.execute(
            update(RepositoryEvent)
            .where(RepositoryEvent.status == RepositoryEventStatus.PROCESSING)
            .values(
                status=RepositoryEventStatus.PENDING,
                processing_token=None,
                processing_started_at=None,
                message="Recovered after the worker restarted.",
            )
        )
        session.commit()
        return recovered.rowcount or 0


def _claim_repository_batch() -> RepositoryBatch | None:
    with SessionLocal() as session:
        pending_events = session.scalars(
            select(RepositoryEvent)
            .where(RepositoryEvent.status == RepositoryEventStatus.PENDING)
            .order_by(RepositoryEvent.id)
            .limit(100)
        ).all()
        if not pending_events:
            return None

        event = None
        service = None
        participant_service = None
        repository_paths: tuple[str, ...] = ()
        for candidate in pending_events:
            candidate_service = session.scalar(
                select(Service).where(
                    or_(
                        Service.repository_path == candidate.repository_path,
                        Service.jury_repository_path == candidate.repository_path,
                    )
                )
            )
            candidate_participant = None
            if candidate_service is not None:
                candidate_paths = tuple(
                    path
                    for path in (
                        candidate_service.repository_path,
                        candidate_service.jury_repository_path,
                    )
                    if path
                )
            else:
                candidate_participant = session.scalar(
                    select(ParticipantService).where(
                        or_(
                            ParticipantService.attack_repository_path == candidate.repository_path,
                            ParticipantService.defense_repository_path == candidate.repository_path,
                        )
                    )
                )
                candidate_paths = (candidate.repository_path,)

            processing_event = session.scalar(
                select(RepositoryEvent.id)
                .where(
                    RepositoryEvent.status == RepositoryEventStatus.PROCESSING,
                    RepositoryEvent.repository_path.in_(candidate_paths),
                )
                .limit(1)
            )
            if processing_event is not None:
                continue

            event = candidate
            service = candidate_service
            participant_service = candidate_participant
            repository_paths = candidate_paths
            break

        if event is None:
            return None

        if service is None:
            if participant_service is None:
                event.status = RepositoryEventStatus.DONE
                event.message = "Repository is no longer managed by SelfAD."
                event.processed_at = datetime.now(timezone.utc)
                session.commit()
                return RepositoryBatch((event.id,), -1, -1)
            service = session.get(Service, participant_service.service_id)
            if service is None:
                event.status = RepositoryEventStatus.DONE
                event.message = "Service no longer exists."
                event.processed_at = datetime.now(timezone.utc)
                session.commit()
                return RepositoryBatch((event.id,), -1, -1)
            claim_token = uuid.uuid4().hex
            session.execute(
                update(RepositoryEvent)
                .where(
                    RepositoryEvent.status == RepositoryEventStatus.PENDING,
                    RepositoryEvent.repository_path == event.repository_path,
                )
                .values(
                    status=RepositoryEventStatus.PROCESSING,
                    attempts=RepositoryEvent.attempts + 1,
                    processing_token=claim_token,
                    processing_started_at=datetime.now(timezone.utc),
                    message="Participant check started.",
                )
            )
            event_ids = tuple(
                session.scalars(
                    select(RepositoryEvent.id)
                    .where(RepositoryEvent.processing_token == claim_token)
                    .order_by(RepositoryEvent.id)
                ).all()
            )
            latest_event = session.scalar(
                select(RepositoryEvent)
                .where(RepositoryEvent.processing_token == claim_token)
                .order_by(RepositoryEvent.id.desc())
            )
            if not event_ids or latest_event is None:
                session.rollback()
                return None
            if event.repository_path == participant_service.attack_repository_path:
                participant_service.attack_status = ParticipantRepositoryStatus.RUNNING
                participant_service.attack_message = "Checking the latest attack push."
            else:
                participant_service.defense_status = ParticipantRepositoryStatus.RUNNING
                participant_service.defense_message = "Checking the latest defense push."
            session.commit()
            return RepositoryBatch(
                event_ids=event_ids,
                service_id=service.id,
                repository_generation=service.repository_generation,
                participant_service_id=participant_service.id,
                repository_path=event.repository_path,
                commit_sha=latest_event.commit_sha,
            )

        claim_token = uuid.uuid4().hex
        session.execute(
            update(RepositoryEvent)
            .where(
                RepositoryEvent.status == RepositoryEventStatus.PENDING,
                RepositoryEvent.repository_path.in_(repository_paths),
            )
            .values(
                status=RepositoryEventStatus.PROCESSING,
                attempts=RepositoryEvent.attempts + 1,
                processing_token=claim_token,
                processing_started_at=datetime.now(timezone.utc),
                message="Repository check started.",
            )
        )
        claimed_events = session.scalars(
            select(RepositoryEvent)
            .where(RepositoryEvent.processing_token == claim_token)
            .order_by(RepositoryEvent.id)
        ).all()
        if not claimed_events:
            session.rollback()
            return None
        event_ids = tuple(claimed_event.id for claimed_event in claimed_events)
        latest_commits = {
            claimed_event.repository_path: claimed_event.commit_sha
            for claimed_event in claimed_events
        }
        session.commit()
        return RepositoryBatch(
            event_ids=event_ids,
            service_id=service.id,
            repository_generation=service.repository_generation,
            source_commit=latest_commits.get(service.repository_path),
            jury_commit=latest_commits.get(service.jury_repository_path or ""),
        )


def _batch_is_superseded(session, batch: RepositoryBatch) -> bool:
    if not batch.repository_path or not batch.event_ids:
        return False
    newer_event = session.scalar(
        select(RepositoryEvent.id)
        .where(
            RepositoryEvent.repository_path == batch.repository_path,
            RepositoryEvent.id > max(batch.event_ids),
        )
        .limit(1)
    )
    return newer_event is not None


def _finish_events(
    session,
    event_ids: tuple[int, ...],
    status: RepositoryEventStatus,
    message: str,
) -> None:
    session.execute(
        update(RepositoryEvent)
        .where(RepositoryEvent.id.in_(event_ids))
        .values(
            status=status,
            processing_token=None,
            processing_started_at=None,
            message=message[:2_000],
            processed_at=datetime.now(timezone.utc),
        )
    )
