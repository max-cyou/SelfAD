import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid, get_csrf_token
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaUnavailable,
    authenticate_gitea_user,
    create_gitea_user,
    delete_gitea_user,
)
from selfad.models import BrandingSettings, InstanceConfig, User
from selfad.security import hash_password
from selfad.settings import get_gitea_settings
from selfad.web import templates


router = APIRouter()

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def render_setup(
    request: Request,
    *,
    site_name: str = "SelfAD",
    admin_username: str = "",
    admin_email: str = "",
    change_title: bool = False,
    remove_standard_logo: bool = False,
    errors: dict[str, str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "title": "Configure SelfAD",
            "site_name": site_name,
            "admin_username": admin_username,
            "admin_email": admin_email,
            "change_title": change_title,
            "remove_standard_logo": remove_standard_logo,
            "errors": errors or {},
            "csrf_token": get_csrf_token(request),
        },
        status_code=status_code,
    )


def validate_setup(
    site_name: str,
    admin_username: str,
    admin_email: str,
    admin_password: str,
    admin_password_confirm: str,
) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not 2 <= len(site_name) <= 120:
        errors["site_name"] = "Use between 2 and 120 characters."

    if not 3 <= len(admin_username) <= 32:
        errors["admin_username"] = "Use between 3 and 32 characters."
    elif not USERNAME_PATTERN.fullmatch(admin_username):
        errors["admin_username"] = (
            "Use letters, numbers, dots, dashes or underscores."
        )
    elif admin_username.lower() == "root":
        errors["admin_username"] = "This username is reserved by the internal Gitea account."

    if len(admin_email) > 320 or not EMAIL_PATTERN.fullmatch(admin_email):
        errors["admin_email"] = "Enter a valid email address."

    if not 10 <= len(admin_password) <= 128:
        errors["admin_password"] = "Use between 10 and 128 characters."

    if admin_password != admin_password_confirm:
        errors["admin_password_confirm"] = "Passwords do not match."

    return errors


@router.get("/setup", response_class=HTMLResponse)
def setup_page(
    request: Request,
    session: Session = Depends(get_session),
):
    config = session.get(InstanceConfig, 1)
    if config and config.setup_complete:
        return RedirectResponse(url="/", status_code=303)

    branding = session.get(BrandingSettings, 1)
    return render_setup(
        request,
        site_name=config.site_name if config else "SelfAD",
        change_title=bool(branding and branding.change_title),
        remove_standard_logo=bool(
            branding and branding.remove_standard_logo
        ),
    )


@router.post("/setup", response_class=HTMLResponse)
def submit_setup(
    request: Request,
    site_name: str = Form(...),
    admin_username: str = Form(...),
    admin_email: str = Form(...),
    admin_password: str = Form(...),
    admin_password_confirm: str = Form(...),
    change_title: bool = Form(False),
    remove_standard_logo: bool = Form(False),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
):
    config = session.get(InstanceConfig, 1)
    if config and config.setup_complete:
        return RedirectResponse(url="/", status_code=303)

    if not csrf_token_is_valid(request, csrf_token):
        return render_setup(
            request,
            errors={"_form": "Invalid setup form. Reload the page and try again."},
            status_code=403,
        )

    site_name = site_name.strip()
    admin_username = admin_username.strip()
    admin_email = admin_email.strip().lower()
    errors = validate_setup(
        site_name,
        admin_username,
        admin_email,
        admin_password,
        admin_password_confirm,
    )

    if errors:
        return render_setup(
            request,
            site_name=site_name,
            admin_username=admin_username,
            admin_email=admin_email,
            change_title=change_title,
            remove_standard_logo=remove_standard_logo,
            errors=errors,
            status_code=422,
        )

    try:
        gitea_settings = get_gitea_settings()
        create_gitea_user(
            gitea_settings,
            username=admin_username,
            email=admin_email,
            password=admin_password,
        )
        gitea_admin = authenticate_gitea_user(
            gitea_settings,
            username=admin_username,
            password=admin_password,
        )
        if gitea_admin is None:
            raise GiteaUnavailable("Gitea rejected the administrator password.")
    except (GiteaConflict, GiteaUnavailable, OSError) as error:
        return render_setup(
            request,
            site_name=site_name,
            admin_username=admin_username,
            admin_email=admin_email,
            change_title=change_title,
            remove_standard_logo=remove_standard_logo,
            errors={"_form": str(error)},
            status_code=502,
        )

    if config is None:
        config = InstanceConfig(id=1)

    branding = session.get(BrandingSettings, 1)
    if branding is None:
        branding = BrandingSettings(id=1)

    config.site_name = site_name
    config.setup_complete = True
    branding.change_title = change_title
    branding.remove_standard_logo = remove_standard_logo

    session.add_all(
        [
            config,
            branding,
            User(
                username=admin_username,
                email=admin_email,
                password_hash=hash_password(admin_password),
                is_admin=True,
                gitea_user_id=gitea_admin.id,
                gitea_username=gitea_admin.username,
            ),
        ]
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        try:
            delete_gitea_user(
                gitea_settings,
                username=gitea_admin.username,
            )
        except GiteaUnavailable:
            pass
        return render_setup(
            request,
            site_name=site_name,
            admin_username=admin_username,
            admin_email=admin_email,
            change_title=change_title,
            remove_standard_logo=remove_standard_logo,
            errors={"admin_username": "This administrator already exists."},
            status_code=409,
        )

    return RedirectResponse(url="/", status_code=303)
