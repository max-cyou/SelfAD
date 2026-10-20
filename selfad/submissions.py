"""Read-only, paginated submission history backed by the retained audit tail."""

from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from selfad.models import (
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    SubmissionAttempt,
    User,
)

PAGE_SIZE = 20
STATUS_LABELS = {
    "passed": "Passed", "failed": "Failed", "error": "Check incomplete",
    "queued": "Queued", "running": "Running",
}


def history_query(user_id: int | None = None):
    attempt = SubmissionAttempt
    passed = and_(
        attempt.completed,
        or_(
            and_(attempt.kind == "attack", attempt.matched_flags > 0),
            and_(attempt.kind == "defense", attempt.functionality_passed,
                 attempt.matched_flags == 0),
        ),
    )
    status = case(
        (~attempt.completed, "error"), (passed, "passed"), else_="failed",
    )
    scored = select(
        literal("attempt").label("record_type"), attempt.id.label("id"),
        attempt.created_at.label("recorded_at"), User.id.label("user_id"),
        User.username.label("username"), Service.id.label("service_id"),
        Service.name.label("service_name"), attempt.kind.label("kind"),
        attempt.commit_sha.label("commit_sha"), status.label("status"),
        attempt.raw_score.label("raw_score"), attempt.penalty.label("penalty"),
        attempt.awarded_score.label("awarded_score"),
        attempt.improved.label("improved"),
        attempt.penalty_eligible.label("penalty_eligible"),
        attempt.matched_flags.label("matched_flags"),
        attempt.injected_flags.label("injected_flags"),
        attempt.functionality_passed.label("functionality_passed"),
        attempt.attempt_number.label("attempt_number"),
        (attempt.message if user_id is None else literal("")).label("message"),
    ).join(ParticipantService, ParticipantService.id == attempt.participant_service_id).join(
        User, User.id == ParticipantService.user_id,
    ).join(Service, Service.id == ParticipantService.service_id)
    event = RepositoryEvent
    pending = select(
        literal("event"), event.id, event.received_at, User.id, User.username,
        Service.id, Service.name,
        case((event.repository_path == ParticipantService.attack_repository_path, "attack"),
             else_="defense"),
        event.commit_sha,
        case((event.status == RepositoryEventStatus.PENDING, "queued"),
             (event.status == RepositoryEventStatus.FAILED, "error"), else_="running"),
        literal(None), literal(None), literal(None), literal(False), literal(False),
        literal(None), literal(None), literal(None), literal(None),
        event.message if user_id is None else literal(""),
    ).join(ParticipantService, or_(
        event.repository_path == ParticipantService.attack_repository_path,
        event.repository_path == ParticipantService.defense_repository_path,
    )).join(User, User.id == ParticipantService.user_id).join(
        Service, Service.id == ParticipantService.service_id,
    ).where(
        event.status.in_([RepositoryEventStatus.PENDING, RepositoryEventStatus.PROCESSING,
                          RepositoryEventStatus.FAILED]),
        event.scored_at.is_(None),
        event.ref == literal("refs/heads/") + Service.default_branch,
    )
    if user_id is not None:
        scored = scored.where(User.id == user_id)
        pending = pending.where(User.id == user_id)
    return union_all(scored, pending).subquery()


def read_history(session: Session, *, user_id: int | None = None,
                 participant: str = "", service_id: int | None = None,
                 kind: str = "", status: str = "", page: int = 1):
    history = history_query(user_id)
    query = select(history)
    if participant:
        escaped = participant.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(history.c.username.ilike(f"%{escaped}%", escape="\\"))
    if service_id is not None:
        query = query.where(history.c.service_id == service_id)
    if kind:
        query = query.where(history.c.kind == kind)
    if status:
        query = query.where(history.c.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    rows = session.execute(query.order_by(
        history.c.recorded_at.desc(), history.c.id.desc(), history.c.record_type,
    ).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).mappings().all()
    service_query = select(Service.id, Service.name).join(
        ParticipantService, ParticipantService.service_id == Service.id,
    ).distinct().order_by(Service.name, Service.id)
    if user_id is not None:
        service_query = service_query.where(ParticipantService.user_id == user_id)
    services = session.execute(service_query).all()
    return rows, services, total, page, pages
