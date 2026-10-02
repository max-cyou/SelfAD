from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from selfad.auth import get_csrf_token, get_session_user
from selfad.branding import get_branding_context
from selfad.contest import contest_state, start_contest_if_due
from selfad.database import get_session
from selfad.models import InstanceConfig
from selfad.web import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: Session = Depends(get_session),
):
    config = session.get(InstanceConfig, 1)
    if start_contest_if_due(config):
        session.commit()
    branding = get_branding_context(session)
    current_user = get_session_user(request, session)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": branding["brand_title"],
            "current_user": current_user,
            "csrf_token": get_csrf_token(request) if current_user else None,
            **branding,
            "contest_state": contest_state(config),
        },
    )
