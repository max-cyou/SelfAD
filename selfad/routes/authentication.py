from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid, get_session_user, sign_in, sign_out
from selfad.branding import get_branding_context
from selfad.database import get_session
from selfad.models import User
from selfad.security import hash_password, verify_password
from selfad.web import templates


router = APIRouter()

DUMMY_PASSWORD_HASH = hash_password("invalid-password-placeholder")


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
    if user and user.is_admin:
        return RedirectResponse(url="/admin", status_code=303)

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
    user = session.scalar(select(User).where(User.username == username))
    password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_is_valid = verify_password(password, password_hash)

    if not user or not user.is_admin or not password_is_valid:
        return render_login(
            request,
            session,
            username=username,
            error="Invalid username or password.",
            status_code=401,
        )

    sign_in(request, user)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not csrf_token_is_valid(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    sign_out(request)
    return RedirectResponse(url="/", status_code=303)
