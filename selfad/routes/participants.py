from collections import defaultdict
from datetime import datetime, timezone
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import (
    csrf_token_is_valid,
    get_csrf_token,
    get_session_user,
    sign_in,
)
from selfad.branding import get_branding_context
from selfad.contest import ENDED, STARTED, contest_state, start_contest_if_due
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaUnavailable,
    add_user_ssh_key,
    create_gitea_user,
    delete_gitea_user,
    gitea_username_exists,
)
from selfad.models import (
    InstanceConfig,
    ParticipantRepositoryStatus,
    ParticipantService,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    User,
)
from selfad.participants import provision_participant_service
from selfad.rate_limit import client_key, rate_limiter
from selfad.security import hash_password, verify_password
from selfad.settings import (
    get_gitea_settings,
    get_gitea_webhook_secret,
    get_rate_limit,
)
from selfad.web import templates


router = APIRouter()
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
DEFAULT_REGISTRATION_FORM = {
    "username": "",
    "email": "",
    "ssh_public_key": "",
}


def _scoreboard_time(value: datetime | None) -> float:
    if value is None:
        return float("inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _scoreboard_sort_key(row: dict[str, object]) -> tuple[object, ...]:
    return (
        -int(row["score"]),
        _scoreboard_time(row["first_solution_at"]),
        _scoreboard_time(row["last_solution_at"]),
        str(row["username"]),
    )


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
    config = session.get(InstanceConfig, 1)
    if start_contest_if_due(config):
        session.commit()
    if not user.is_admin and contest_state(config) != STARTED:
        return None, RedirectResponse(url="/", status_code=303)
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
        .order_by(Service.id)
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
    has_checking = any(
        assignment.attack_status == ParticipantRepositoryStatus.RUNNING
        or assignment.defense_status == ParticipantRepositoryStatus.RUNNING
        for assignment, _ in assignments
    )

    return templates.TemplateResponse(
        request=request,
        name="services.html",
        context={
            "title": f"Services · {branding['brand_title']}",
            "current_user": user,
            "cards": cards,
            "has_checking": has_checking,
            "csrf_token": get_csrf_token(request),
            **branding,
        },
    )


def render_scoreboard(
    request: Request,
    session: Session,
    *,
    status_code: int = 200,
):
    branding = get_branding_context(session)
    current_user = get_session_user(request, session)
    active_services = session.scalars(
        select(Service)
        .where(Service.status == ServiceStatus.ACTIVE)
        .order_by(Service.id)
    ).all()
    active_service_ids = {service.id for service in active_services}
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
    scores_by_user_service: dict[tuple[int, int], dict[str, int]] = {}
    for assignment in assignments:
        if assignment.service_id not in active_service_ids:
            continue
        total = totals[assignment.user_id]
        total["attack"] += assignment.attack_score
        total["defense"] += assignment.defense_score
        total["services"] += 1
        scores_by_user_service[(assignment.user_id, assignment.service_id)] = {
            "attack": assignment.attack_score,
            "defense": assignment.defense_score,
        }

    active_assignments = [
        assignment
        for assignment in assignments
        if assignment.service_id in active_service_ids
    ]
    first_solution_by_user = {}
    last_solution_by_user = {}
    for assignment in active_assignments:
        if assignment.first_awarded_at is None:
            continue
        user_id = assignment.user_id
        first = first_solution_by_user.get(user_id)
        last = last_solution_by_user.get(user_id)
        if first is None or assignment.first_awarded_at < first:
            first_solution_by_user[user_id] = assignment.first_awarded_at
        if assignment.last_awarded_at is None:
            continue
        if last is None or assignment.last_awarded_at > last:
            last_solution_by_user[user_id] = assignment.last_awarded_at

    rows = [
        {
            "username": participant.username,
            "attack": totals[user_id]["attack"],
            "defense": totals[user_id]["defense"],
            "services": totals[user_id]["services"],
            "score": totals[user_id]["attack"] + totals[user_id]["defense"],
            "first_solution_at": first_solution_by_user.get(user_id),
            "last_solution_at": last_solution_by_user.get(user_id),
            "service_scores": [
                scores_by_user_service.get(
                    (user_id, service.id),
                    {"attack": 0, "defense": 0},
                )
                for service in active_services
            ],
        }
        for user_id, participant in participants.items()
        if totals[user_id]["attack"] + totals[user_id]["defense"] >= 1
    ]
    rows.sort(key=_scoreboard_sort_key)
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
            "active_services": active_services,
            "active_service_count": len(active_service_ids),
            "csrf_token": get_csrf_token(request) if current_user else None,
            **branding,
        },
        status_code=status_code,
    )


@router.get("/scoreboard", response_class=HTMLResponse)
def scoreboard(
    request: Request,
    session: Session = Depends(get_session),
):
    config = session.get(InstanceConfig, 1)
    if start_contest_if_due(config):
        session.commit()
    current_user = get_session_user(request, session)
    if (
        contest_state(config) not in {STARTED, ENDED}
        and not (current_user and current_user.is_admin)
    ):
        return RedirectResponse(url="/", status_code=303)
    return render_scoreboard(request, session)


def validate_registration(
    values: dict[str, str],
    password: str,
    password_confirm: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not USERNAME_PATTERN.fullmatch(values["username"]):
        errors["username"] = (
            "Use 3–32 letters, numbers, dots, dashes or underscores."
        )
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"]):
        errors["email"] = "Enter a valid email address."
    if not 10 <= len(password) <= 128:
        errors["password"] = "Use between 10 and 128 characters."
    if password != password_confirm:
        errors["password_confirm"] = "Passwords do not match."
    key = values["ssh_public_key"]
    if key and (
        len(key) > 2048
        or not key.startswith(("ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-"))
    ):
        errors["ssh_public_key"] = "Enter a valid SSH public key."
    return errors


def render_registration(
    request: Request,
    session: Session,
    *,
    registration_errors: dict[str, str] | None = None,
    registration_form: dict[str, str] | None = None,
    status_code: int = 200,
):
    branding = get_branding_context(session)
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "title": f"Create account · {branding['brand_title']}",
            "csrf_token": get_csrf_token(request),
            "registration_errors": registration_errors or {},
            "registration_form": registration_form or DEFAULT_REGISTRATION_FORM,
            **branding,
        },
        status_code=status_code,
    )


@router.get("/register", response_class=HTMLResponse)
def registration_page(
    request: Request,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)
    if not branding["registration_enabled"]:
        raise HTTPException(status_code=404, detail="Registration is disabled.")
    config = session.get(InstanceConfig, 1)
    if contest_state(config) == ENDED:
        raise HTTPException(status_code=404, detail="Registration is closed.")
    if get_session_user(request, session):
        return RedirectResponse(
            url="/services" if contest_state(config) == STARTED else "/",
            status_code=303,
        )
    return render_registration(request, session)


@router.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["registration_enabled"]:
        raise HTTPException(status_code=404, detail="Registration is disabled.")
    config = session.get(InstanceConfig, 1)
    if contest_state(config) == ENDED:
        raise HTTPException(status_code=404, detail="Registration is closed.")
    if get_session_user(request, session):
        return RedirectResponse(
            url="/services" if contest_state(config) == STARTED else "/",
            status_code=303,
        )

    client_host = request.client.host if request.client else None
    if not rate_limiter.allow(
        client_key(client_host, "registration"),
        limit=get_rate_limit("SELFAD_REGISTRATION_RATE_LIMIT", default=6),
        window_seconds=60,
    ):
        return HTMLResponse(
            "Too many registration attempts. Try again in a minute.",
            status_code=429,
        )

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    values = {
        "username": str(form.get("username", "")).strip(),
        "email": str(form.get("email", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
    }
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))
    errors = validate_registration(values, password, password_confirm)
    invite_code = str(form.get("invite_code", ""))
    if (
        config
        and config.registration_invite_only
        and (
            not config.registration_invite_code_hash
            or not verify_password(invite_code, config.registration_invite_code_hash)
        )
    ):
        errors["invite_code"] = "Invalid invite code."
    if session.scalar(select(User.id).where(User.username == values["username"])):
        errors["username"] = "This username is already in use."
    if session.scalar(select(User.id).where(User.email == values["email"])):
        errors["email"] = "This email is already in use."
    settings = get_gitea_settings()
    if not errors:
        try:
            taken_in_gitea = await run_in_threadpool(
                gitea_username_exists,
                settings,
                username=values["username"],
            )
        except GiteaUnavailable as error:
            return render_registration(
                request,
                session,
                registration_errors={"_form": str(error)},
                registration_form=values,
                status_code=502,
            )
        if taken_in_gitea:
            # Covers reserved Gitea accounts (root, the organiser) that have
            # no SelfAD row and therefore pass the local uniqueness checks.
            errors["username"] = "This username is already in use."
    if errors:
        return render_registration(
            request,
            session,
            registration_errors=errors,
            registration_form=values,
            status_code=422,
        )

    gitea_user = None
    ssh_key_id = None
    try:
        gitea_user = await run_in_threadpool(
            create_gitea_user,
            settings,
            username=values["username"],
            email=values["email"],
            password=password,
        )
        if values["ssh_public_key"]:
            ssh_key_id = await run_in_threadpool(
                add_user_ssh_key,
                settings,
                username=gitea_user.username,
                public_key=values["ssh_public_key"],
            )
    except GiteaConflict as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(
                    delete_gitea_user,
                    settings,
                    username=gitea_user.username,
                )
            except GiteaError:
                pass
        # A conflict means Gitea rejected the input itself (username taken,
        # malformed SSH key): that is a form error, not a gateway failure.
        return render_registration(
            request,
            session,
            registration_errors={
                "username" if gitea_user is None else "ssh_public_key": str(
                    error
                )
            },
            registration_form=values,
            status_code=422,
        )
    except GiteaUnavailable as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(
                    delete_gitea_user,
                    settings,
                    username=gitea_user.username,
                )
            except GiteaError:
                pass
        return render_registration(
            request,
            session,
            registration_errors={"_form": str(error)},
            registration_form=values,
            status_code=502,
        )

    user = User(
        username=values["username"],
        email=values["email"],
        password_hash=hash_password(password),
        ssh_public_key=values["ssh_public_key"],
        git_ssh_key_id=ssh_key_id,
        gitea_user_id=gitea_user.id,
        gitea_username=gitea_user.username,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        try:
            await run_in_threadpool(
                delete_gitea_user,
                settings,
                username=gitea_user.username,
            )
        except GiteaError:
            pass
        return render_registration(
            request,
            session,
            registration_errors={"_form": "The account could not be created."},
            registration_form=values,
            status_code=409,
        )

    active_services = session.scalars(
        select(Service).where(
            Service.status == ServiceStatus.ACTIVE,
            Service.runtime_status == ServiceRunStatus.PASSED,
        )
    ).all()
    for service in active_services:
        try:
            assignment = await run_in_threadpool(
                provision_participant_service,
                settings,
                service=service,
                user=user,
                webhook_secret=get_gitea_webhook_secret(),
            )
        except GiteaError:
            continue
        session.add(assignment)
        session.commit()

    sign_in(request, user)
    return RedirectResponse(
        url="/services" if contest_state(config) == STARTED else "/",
        status_code=303,
    )
