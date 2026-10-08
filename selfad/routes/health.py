import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from selfad.database import engine, get_session
from selfad.gitea import GiteaError, get_authenticated_user
from selfad.models import InstanceConfig, ParticipantService, RepositoryEvent
from selfad.runner import runner_is_available, runner_mode
from selfad.settings import get_gitea_settings, get_metrics_token

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/internal/gitea-public-access", include_in_schema=False)
def gitea_public_access(session: Session = Depends(get_session)):
    config = session.get(InstanceConfig, 1)
    return Response(
        status_code=204 if config and config.gitea_public_enabled else 403,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/ready")
def readiness():
    database_ready = False
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_ready = True
    except Exception:
        pass
    gitea_ready = False
    try:
        get_authenticated_user(get_gitea_settings())
        gitea_ready = True
    except GiteaError:
        pass
    runner_ready = runner_is_available()
    payload = {
        "status": (
            "ok" if database_ready and gitea_ready and runner_ready else "degraded"
        ),
        "database": database_ready,
        "gitea": gitea_ready,
        "runner": runner_ready,
        "runner_mode": runner_mode(),
    }
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(
    request: Request,
    session: Session = Depends(get_session),
):
    token = get_metrics_token()
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if token is None or not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=404, detail="Not found.")

    events = dict(
        session.execute(
            select(RepositoryEvent.status, func.count())
            .group_by(RepositoryEvent.status)
        ).all()
    )
    assignments = session.scalar(
        select(func.count()).select_from(ParticipantService)
    ) or 0
    now = datetime.now(timezone.utc)

    def event_age(status: str, timestamp_column) -> float:
        oldest = session.scalar(
            select(func.min(timestamp_column)).where(
                RepositoryEvent.status == status
            )
        )
        if oldest is None:
            return 0.0
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        return max(0.0, (now - oldest).total_seconds())

    oldest_pending_age = event_age("pending", RepositoryEvent.received_at)
    oldest_processing_age = event_age(
        "processing",
        RepositoryEvent.processing_started_at,
    )
    lines = [
        "# HELP selfad_repository_events Number of repository events by state.",
        "# TYPE selfad_repository_events gauge",
    ]
    for status in ("pending", "processing", "done", "failed"):
        value = events.get(status, events.get(getattr(status, "value", status), 0))
        lines.append(f'selfad_repository_events{{status="{status}"}} {value}')
    lines.extend(
        [
            "# HELP selfad_participant_services Number of issued participant services.",
            "# TYPE selfad_participant_services gauge",
            f"selfad_participant_services {assignments}",
            "# HELP selfad_oldest_repository_event_seconds Age of the oldest queue event.",
            "# TYPE selfad_oldest_repository_event_seconds gauge",
            (
                "selfad_oldest_repository_event_seconds"
                f'{{status="pending"}} {oldest_pending_age:.3f}'
            ),
            (
                "selfad_oldest_repository_event_seconds"
                f'{{status="processing"}} {oldest_processing_age:.3f}'
            ),
            "# HELP selfad_runner_mode Runner deployment mode (1 for current mode).",
            "# TYPE selfad_runner_mode gauge",
            f'selfad_runner_mode{{mode="{runner_mode()}"}} 1',
        ]
    )
    return PlainTextResponse("\n".join(lines) + "\n")
