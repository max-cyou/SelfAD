from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from selfad.auth import (
    csrf_token_is_valid,
    get_csrf_token,
    get_session_user,
    sign_in,
    sign_out,
)
from selfad.branding import get_branding_context
from selfad.contest import STARTED, contest_state
from selfad.database import get_session
from selfad.gitea import GiteaUnavailable, authenticate_gitea_user
from selfad.models import InstanceConfig, User
from selfad.rate_limit import client_key, rate_limiter
from selfad.settings import get_gitea_settings, get_rate_limit
from selfad.web import templates


router = APIRouter()


def render_login(
    request: Request,
    session: Session,
    *,
    username: str = "",
    error: str | None = None,
    status_code: int = 200,
):
    branding = get_branding_context(session)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "title": f"Sign in · {branding['brand_title']}",
            "username": username,
            "error": error,
            "csrf_token": get_csrf_token(request),
            **branding,
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)

    user = get_session_user(request, session)
    if user:
        config = session.get(InstanceConfig, 1)
        return RedirectResponse(
            url=(
                "/admin"
                if user.is_admin
                else ("/services" if contest_state(config) == STARTED else "/")
            ),
            status_code=303,
        )

    return render_login(request, session)


@router.post("/login", response_class=HTMLResponse)
def submit_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)

    username = username.strip()
    if not csrf_token_is_valid(request, csrf_token):
        return render_login(
            request,
            session,
            username=username,
            error="Invalid sign-in form. Reload the page and try again.",
            status_code=403,
        )
    client_host = request.client.host if request.client else None
    if not rate_limiter.allow(
        client_key(client_host, f"login:{username.lower()[:32]}"),
        limit=get_rate_limit("SELFAD_LOGIN_RATE_LIMIT", default=12),
        window_seconds=60,
    ):
        return render_login(
            request,
            session,
            username=username,
            error="Too many sign-in attempts. Try again in a minute.",
            status_code=429,
        )
    local_user = session.scalar(select(User).where(User.username == username))
    gitea_username = (
        local_user.gitea_username
        if local_user and local_user.gitea_username
        else username
    )
    try:
        gitea_user = authenticate_gitea_user(
            get_gitea_settings(),
            username=gitea_username,
            password=password,
        )
    except GiteaUnavailable:
        return render_login(
            request,
            session,
            username=username,
            error="Gitea authentication is temporarily unavailable.",
            status_code=503,
        )

    user = local_user
    if gitea_user:
        if user is None:
            user = session.scalar(
                select(User).where(
                    or_(
                        User.gitea_user_id == gitea_user.id,
                        User.gitea_username == gitea_user.username,
                    )
                )
            )
    if gitea_user is None or user is None:
        return render_login(
            request,
            session,
            username=username,
            error="Invalid username or password.",
            status_code=401,
        )

    if (
        user.gitea_user_id != gitea_user.id
        or user.gitea_username != gitea_user.username
    ):
        user.gitea_user_id = gitea_user.id
        user.gitea_username = gitea_user.username
        session.commit()

    sign_in(request, user)
    config = session.get(InstanceConfig, 1)
    return RedirectResponse(
        url=(
            "/admin"
            if user.is_admin
            else ("/services" if contest_state(config) == STARTED else "/")
        ),
        status_code=303,
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not csrf_token_is_valid(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    sign_out(request)
    return RedirectResponse(url="/", status_code=303)
