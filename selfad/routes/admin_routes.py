from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from selfad.database import get_session
from selfad.routes import admin_services, admin_settings, admin_users
from selfad.routes.admin_shared import get_admin_access, render_admin

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    saved: bool = False,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

    return render_admin(
        request,
        session,
        user,
        saved=saved,
        appearance_mode=request.query_params.get("appearance_mode"),
        active_section=(
            "users"
            if (
                "users_page" in request.query_params
                or "users_search" in request.query_params
            )
            else ("appearance" if saved else "general")
        ),
    )

router.include_router(admin_services.router)
router.include_router(admin_users.router)
router.include_router(admin_settings.router)
