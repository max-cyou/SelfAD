from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid, get_session_user, sign_in, sign_out
from selfad.branding import get_branding_context
from selfad.database import get_session
from selfad.gitea import GiteaUnavailable, authenticate_gitea_user
from selfad.models import User
from selfad.settings import get_gitea_settings
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
        return RedirectResponse(
            url="/admin" if user.is_admin else "/services",
            status_code=303,
        )

    return render_login(request, session)


@router.post("/login", response_class=HTMLResponse)
def submit_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)

    username = username.strip()
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
    return RedirectResponse(
        url="/admin" if user.is_admin else "/services",
        status_code=303,
    )


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not csrf_token_is_valid(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    sign_out(request)
    return RedirectResponse(url="/", status_code=303)
