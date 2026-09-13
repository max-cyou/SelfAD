from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from selfad.database import get_session
from selfad.models import BrandingSettings, InstanceConfig
from selfad.web import templates


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session: Session = Depends(get_session),
):
    config = session.get(InstanceConfig, 1)
    branding = session.get(BrandingSettings, 1)
    is_configured = bool(config and config.setup_complete)
    use_custom_title = bool(
        is_configured and branding and branding.change_title
    )
    brand_title = config.site_name if use_custom_title else "SelfAD"
    show_standard_logo = not bool(
        is_configured and branding and branding.remove_standard_logo
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": brand_title,
            "site_name": config.site_name if is_configured else "SelfAD",
            "brand_title": brand_title,
            "show_standard_logo": show_standard_logo,
            "is_configured": is_configured,
        },
    )
