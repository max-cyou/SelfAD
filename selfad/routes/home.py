from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from selfad.branding import get_branding_context
from selfad.database import get_session
from selfad.web import templates


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": branding["brand_title"],
            **branding,
        },
    )
