import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from selfad.gitea import GiteaError, get_authenticated_user
from selfad.runner import runner_is_available, runner_mode
from selfad.database import get_session
from selfad.models import ParticipantService, RepositoryEvent
from selfad.settings import get_gitea_settings, get_metrics_token


router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
def readiness():
    gitea_ready = False
    try:
        get_authenticated_user(get_gitea_settings())
        gitea_ready = True
    except GiteaError:
        pass
    runner_ready = runner_is_available()
    payload = {
        "status": "ok" if gitea_ready and runner_ready else "degraded",
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
            "# HELP selfad_runner_mode Runner deployment mode (1 for current mode).",
            "# TYPE selfad_runner_mode gauge",
            f'selfad_runner_mode{{mode="{runner_mode()}"}} 1',
        ]
    )
    return PlainTextResponse("\n".join(lines) + "\n")
