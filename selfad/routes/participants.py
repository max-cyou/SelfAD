from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from selfad.auth import get_csrf_token, get_session_user
from selfad.branding import get_branding_context
from selfad.database import get_session
from selfad.models import ParticipantService, Service, ServiceStatus, User
from selfad.settings import get_gitea_settings
from selfad.web import templates


router = APIRouter()


def participant_access(
    request: Request,
    session: Session,
) -> tuple[User | None, RedirectResponse | None]:
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return None, RedirectResponse(url="/setup", status_code=303)

    user = get_session_user(request, session)
    if user is None:
        return None, RedirectResponse(url="/login", status_code=303)
    return user, None


def repository_url(public_url: str, repository_path: str) -> str:
    return f"{public_url}/{repository_path}"


@router.get("/services", response_class=HTMLResponse)
def participant_services(
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = participant_access(request, session)
    if redirect:
        return redirect

    branding = get_branding_context(session)
    settings = get_gitea_settings()
    assignments = session.execute(
        select(ParticipantService, Service)
        .join(Service, Service.id == ParticipantService.service_id)
        .where(
            ParticipantService.user_id == user.id,
            Service.status == ServiceStatus.ACTIVE,
        )
        .order_by(Service.name, Service.id)
    ).all()
    cards = [
        {
            "assignment": assignment,
            "service": service,
            "source_url": repository_url(settings.public_url, service.repository_path),
            "attack_url": repository_url(
                settings.public_url, assignment.attack_repository_path
            ),
            "defense_url": repository_url(
                settings.public_url, assignment.defense_repository_path
            ),
        }
        for assignment, service in assignments
    ]

    return templates.TemplateResponse(
        request=request,
        name="services.html",
        context={
            "title": f"Services · {branding['brand_title']}",
            "current_user": user,
            "cards": cards,
            "csrf_token": get_csrf_token(request),
            **branding,
        },
    )


@router.get("/scoreboard", response_class=HTMLResponse)
def scoreboard(
    request: Request,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    current_user = get_session_user(request, session)
    active_service_ids = set(
        session.scalars(
            select(Service.id).where(Service.status == ServiceStatus.ACTIVE)
        ).all()
    )
    assignments = session.scalars(select(ParticipantService)).all()
    user_ids = {
        assignment.user_id
        for assignment in assignments
        if assignment.service_id in active_service_ids
    }
    participants = {
        user.id: user
        for user in session.scalars(
            select(User).where(User.id.in_(user_ids)).order_by(User.username)
        ).all()
    }
    totals: dict[int, dict[str, int]] = defaultdict(
        lambda: {"attack": 0, "defense": 0, "services": 0}
    )
    for assignment in assignments:
        if assignment.service_id not in active_service_ids:
            continue
        total = totals[assignment.user_id]
        total["attack"] += assignment.attack_score
        total["defense"] += assignment.defense_score
        total["services"] += 1

    rows = [
        {
            "username": participant.username,
            "attack": totals[user_id]["attack"],
            "defense": totals[user_id]["defense"],
            "services": totals[user_id]["services"],
            "score": totals[user_id]["attack"] + totals[user_id]["defense"],
        }
        for user_id, participant in participants.items()
    ]
    rows.sort(key=lambda row: (-row["score"], -row["attack"], row["username"]))
    previous_score: int | None = None
    current_rank = 0
    for index, row in enumerate(rows, start=1):
        if row["score"] != previous_score:
            current_rank = index
            previous_score = row["score"]
        row["rank"] = current_rank

    return templates.TemplateResponse(
        request=request,
        name="scoreboard.html",
        context={
            "title": f"Scoreboard · {branding['brand_title']}",
            "current_user": current_user,
            "rows": rows,
            "active_service_count": len(active_service_ids),
            "csrf_token": get_csrf_token(request) if current_user else None,
            **branding,
        },
    )
