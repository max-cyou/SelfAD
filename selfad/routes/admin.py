from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid, get_csrf_token, get_session_user
from selfad.branding import (
    HEX_COLOR_PATTERN,
    PALETTE_FIELDS,
    get_branding_context,
)
from selfad.database import get_session
from selfad.models import BrandingSettings, InstanceConfig, PaletteSettings
from selfad.web import templates


router = APIRouter()


def render_admin(
    request: Request,
    session: Session,
    user,
    *,
    saved: bool = False,
    errors: dict[str, str] | None = None,
    form_values: dict[str, object] | None = None,
    active_section: str = "services",
    status_code: int = 200,
):
    branding = get_branding_context(session)
    values = {
        "site_name": branding["site_name"],
        "change_title": branding["change_title"],
        "remove_standard_logo": branding["remove_standard_logo"],
        **branding["palette_values"],
    }
    if form_values:
        values.update(form_values)

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "title": f"Admin · {branding['brand_title']}",
            "current_user": user,
            "saved": saved,
            "errors": errors or {},
            "form_values": values,
            "palette_fields": PALETTE_FIELDS,
            "csrf_token": get_csrf_token(request),
            "active_section": active_section,
            **branding,
        },
        status_code=status_code,
    )


@router.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    saved: bool = False,
    session: Session = Depends(get_session),
):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)

    user = get_session_user(request, session)
    if not user or not user.is_admin:
        return RedirectResponse(url="/login", status_code=303)

    return render_admin(
        request,
        session,
        user,
        saved=saved,
        active_section="appearance" if saved else "services",
    )


@router.post("/admin/appearance", response_class=HTMLResponse)
async def update_appearance(
    request: Request,
    session: Session = Depends(get_session),
):
    branding_context = get_branding_context(session)
    if not branding_context["is_configured"]:
        return RedirectResponse(url="/setup", status_code=303)

    user = get_session_user(request, session)
    if not user or not user.is_admin:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    site_name = str(form.get("site_name", "")).strip()
    palette_values = {
        name: str(form.get(name, "")).strip().upper()
        for name in PALETTE_FIELDS
    }
    errors: dict[str, str] = {}
    if not 2 <= len(site_name) <= 120:
        errors["site_name"] = "Use between 2 and 120 characters."

    for name, value in palette_values.items():
        if not HEX_COLOR_PATTERN.fullmatch(value):
            errors[name] = "Use #RRGGBB."

    form_values: dict[str, object] = {
        "site_name": site_name,
        "change_title": form.get("change_title") == "on",
        "remove_standard_logo": form.get("remove_standard_logo") == "on",
        **palette_values,
    }
    if errors:
        return render_admin(
            request,
            session,
            user,
            errors=errors,
            form_values=form_values,
            active_section="appearance",
            status_code=422,
        )

    config = session.get(InstanceConfig, 1)
    branding = session.get(BrandingSettings, 1)
    palette = session.get(PaletteSettings, 1)
    if config is None or branding is None:
        return RedirectResponse(url="/setup", status_code=303)
    if palette is None:
        palette = PaletteSettings(id=1, **palette_values)

    config.site_name = site_name
    branding.change_title = bool(form_values["change_title"])
    branding.remove_standard_logo = bool(
        form_values["remove_standard_logo"]
    )
    for name, value in palette_values.items():
        setattr(palette, name, value)

    session.add_all([config, branding, palette])
    session.commit()

    return RedirectResponse(url="/admin?saved=true#appearance", status_code=303)
