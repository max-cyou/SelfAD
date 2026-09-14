import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import csrf_token_is_valid, get_csrf_token, get_session_user
from selfad.branding import (
    HEX_COLOR_PATTERN,
    PALETTE_FIELDS,
    get_branding_context,
)
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaRepositoryNotFound,
    GiteaUnavailable,
    delete_repository,
    get_repository,
    provision_service,
    repository_file_exists,
)
from selfad.models import (
    BrandingSettings,
    InstanceConfig,
    PaletteSettings,
    Service,
    ServiceStatus,
    User,
)
from selfad.settings import get_gitea_root_password, get_gitea_settings
from selfad.web import templates


router = APIRouter()

SERVICE_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

DEFAULT_SERVICE_FORM = {
    "name": "",
    "slug": "",
    "description": "",
    "default_branch": "main",
    "status": ServiceStatus.DRAFT.value,
    "ssh_public_key": "",
}


def get_admin_access(
    request: Request,
    session: Session,
) -> tuple[User | None, RedirectResponse | None]:
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return None, RedirectResponse(url="/setup", status_code=303)

    user = get_session_user(request, session)
    if not user or not user.is_admin:
        return None, RedirectResponse(url="/login", status_code=303)

    return user, None


def render_admin(
    request: Request,
    session: Session,
    user: User,
    *,
    saved: bool = False,
    errors: dict[str, str] | None = None,
    form_values: dict[str, object] | None = None,
    service_errors: dict[str, str] | None = None,
    service_form: dict[str, str] | None = None,
    service_edit_id: int | None = None,
    active_section: str = "services",
    status_code: int = 200,
):
    branding = get_branding_context(session)
    appearance_values = {
        "site_name": branding["site_name"],
        "change_title": branding["change_title"],
        "remove_standard_logo": branding["remove_standard_logo"],
        **branding["palette_values"],
    }
    if form_values:
        appearance_values.update(form_values)

    services = session.scalars(
        select(Service).order_by(Service.name, Service.id)
    ).all()
    gitea_settings = get_gitea_settings()

    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "title": f"Admin · {branding['brand_title']}",
            "current_user": user,
            "saved": saved,
            "errors": errors or {},
            "form_values": appearance_values,
            "palette_fields": PALETTE_FIELDS,
            "csrf_token": get_csrf_token(request),
            "active_section": active_section,
            "services": services,
            "service_errors": service_errors or {},
            "service_form": service_form or DEFAULT_SERVICE_FORM,
            "service_edit_id": service_edit_id,
            "ssh_key_required": not bool(user.ssh_public_key),
            "gitea_configured": gitea_settings.configured,
            "gitea_public_url": gitea_settings.public_url,
            **branding,
        },
        status_code=status_code,
    )


def parse_service_form(form) -> dict[str, str]:
    return {
        "name": str(form.get("name", "")).strip(),
        "slug": str(form.get("slug", "")).strip().lower(),
        "description": str(form.get("description", "")).strip(),
        "default_branch": str(form.get("default_branch", "")).strip(),
        "status": str(form.get("status", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
    }


def validate_service_form(
    values: dict[str, str],
    *,
    require_ssh_key: bool = False,
) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not 2 <= len(values["name"]) <= 120:
        errors["name"] = "Use between 2 and 120 characters."

    if not 2 <= len(values["slug"]) <= 64:
        errors["slug"] = "Use between 2 and 64 characters."
    elif not SERVICE_SLUG_PATTERN.fullmatch(values["slug"]):
        errors["slug"] = "Use lowercase letters, numbers and single dashes."

    description = values["description"]
    if len(description) > 4000:
        errors["description"] = "Use no more than 4000 characters."

    branch = values["default_branch"]
    if (
        not BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or "//" in branch
        or branch.endswith(("/", ".", ".lock"))
    ):
        errors["default_branch"] = "Enter a valid Git branch name."

    if values["status"] not in {item.value for item in ServiceStatus}:
        errors["status"] = "Select draft or active."

    ssh_public_key = values["ssh_public_key"]
    if require_ssh_key and not ssh_public_key:
        errors["ssh_public_key"] = "Add your SSH public key."
    elif ssh_public_key and (
        len(ssh_public_key) > 2048
        or not ssh_public_key.startswith(
            ("ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-")
        )
    ):
        errors["ssh_public_key"] = "Enter a valid SSH public key."

    return errors


def add_service_uniqueness_errors(
    session: Session,
    values: dict[str, str],
    errors: dict[str, str],
    *,
    exclude_id: int | None = None,
) -> None:
    slug_query = select(Service.id).where(Service.slug == values["slug"])
    if exclude_id is not None:
        slug_query = slug_query.where(Service.id != exclude_id)

    if "slug" not in errors and session.scalar(slug_query) is not None:
        errors["slug"] = "This slug is already in use."


async def verify_service_projects(
    service: Service,
    errors: dict[str, str],
) -> None:
    settings = get_gitea_settings()
    if (
        not settings.configured
        or service.repository_path is None
        or service.jury_repository_path is None
    ):
        errors["status"] = "The service and jury repositories must be provisioned."
        return

    try:
        service_repository = await run_in_threadpool(
            get_repository,
            settings,
            service.repository_path,
        )
        jury_repository = await run_in_threadpool(
            get_repository,
            settings,
            service.jury_repository_path,
        )
        if service_repository.empty or jury_repository.empty:
            errors["status"] = (
                "Push files to both Gitea repositories before activation."
            )
            return
        has_dockerfile = await run_in_threadpool(
            repository_file_exists,
            settings,
            service.repository_path,
            "Dockerfile",
            ref=service.default_branch,
        )
        if not has_dockerfile:
            errors["status"] = (
                "The service repository needs a Dockerfile on the default branch."
            )
    except GiteaRepositoryNotFound:
        errors["status"] = "A provisioned Gitea repository is missing."
    except GiteaUnavailable as error:
        errors["_form"] = str(error)


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
        active_section="appearance" if saved else "services",
    )


@router.post("/admin/services", response_class=HTMLResponse)
async def create_service(
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    values = parse_service_form(form)
    errors = validate_service_form(
        values,
        require_ssh_key=not bool(user.ssh_public_key),
    )
    add_service_uniqueness_errors(session, values, errors)
    if values["status"] == ServiceStatus.ACTIVE.value:
        errors["status"] = "Create the repositories as draft, then add their files."

    settings = get_gitea_settings()
    if not settings.configured:
        errors["_form"] = "Configure the local Gitea API token first."
    if errors:
        return render_admin(
            request,
            session,
            user,
            service_errors=errors,
            service_form=values,
            active_section="services",
            status_code=422,
        )

    ssh_public_key = user.ssh_public_key or values["ssh_public_key"]
    try:
        provisioned = await run_in_threadpool(
            provision_service,
            settings,
            name=values["name"],
            slug=values["slug"],
            description=values["description"],
            default_branch=values["default_branch"],
            ssh_public_key=ssh_public_key,
        )
    except (GiteaConflict, GiteaUnavailable) as error:
        return render_admin(
            request,
            session,
            user,
            service_errors={"_form": str(error)},
            service_form=values,
            active_section="services",
            status_code=409 if isinstance(error, GiteaConflict) else 502,
        )

    user.ssh_public_key = ssh_public_key
    user.git_ssh_key_id = provisioned.ssh_key_id
    session.add(
        Service(
            name=values["name"],
            slug=values["slug"],
            description=values["description"],
            repository_id=provisioned.service_repository.id,
            repository_path=provisioned.service_repository.path,
            jury_repository_id=provisioned.jury_repository.id,
            jury_repository_path=provisioned.jury_repository.path,
            default_branch=values["default_branch"],
            status=ServiceStatus.DRAFT,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        for repository_path in (
            provisioned.jury_repository.path,
            provisioned.service_repository.path,
        ):
            try:
                await run_in_threadpool(
                    delete_repository,
                    settings,
                    repository_path,
                )
            except GiteaError:
                pass
        return render_admin(
            request,
            session,
            user,
            service_errors={"_form": "The service could not be saved."},
            service_form=values,
            active_section="services",
            status_code=409,
        )

    return RedirectResponse(url="/admin#services", status_code=303)


@router.post("/admin/services/{service_id}", response_class=HTMLResponse)
async def update_service(
    service_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

    service = session.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    values = parse_service_form(form)
    errors = validate_service_form(values)
    if values["slug"] != service.slug:
        errors["slug"] = "Slug cannot change after repositories are created."
    if values["default_branch"] != service.default_branch:
        errors["default_branch"] = (
            "Default branch cannot change after repositories are created."
        )
    add_service_uniqueness_errors(
        session,
        values,
        errors,
        exclude_id=service.id,
    )
    if values["status"] == ServiceStatus.ACTIVE.value:
        await verify_service_projects(service, errors)
    if errors:
        return render_admin(
            request,
            session,
            user,
            service_errors=errors,
            service_form=values,
            service_edit_id=service.id,
            active_section="services",
            status_code=422,
        )

    service.name = values["name"]
    service.slug = values["slug"]
    service.description = values["description"]
    service.default_branch = values["default_branch"]
    service.status = ServiceStatus(values["status"])
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return render_admin(
            request,
            session,
            user,
            service_errors={"_form": "The slug or Gitea repository is already in use."},
            service_form=values,
            service_edit_id=service.id,
            active_section="services",
            status_code=409,
        )

    return RedirectResponse(url="/admin#services", status_code=303)


@router.post("/admin/services/{service_id}/delete")
async def delete_service(
    service_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

    service = session.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    settings = get_gitea_settings()
    for repository_path in (
        service.jury_repository_path,
        service.repository_path,
    ):
        if repository_path is None:
            continue
        try:
            await run_in_threadpool(
                delete_repository,
                settings,
                repository_path,
            )
        except GiteaUnavailable as error:
            return render_admin(
                request,
                session,
                user,
                service_errors={"_form": str(error)},
                active_section="services",
                status_code=502,
            )

    session.delete(service)
    session.commit()
    return RedirectResponse(url="/admin#services", status_code=303)


@router.post("/admin/gitea-credentials")
async def gitea_credentials(
    request: Request,
    session: Session = Depends(get_session),
):
    _, redirect = get_admin_access(request, session)
    if redirect:
        raise HTTPException(status_code=403, detail="Administrator access required.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    password = get_gitea_root_password()
    if password is None:
        raise HTTPException(status_code=503, detail="Gitea password is unavailable.")

    return JSONResponse(
        {"username": "root", "password": password},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/appearance", response_class=HTMLResponse)
async def update_appearance(
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

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
