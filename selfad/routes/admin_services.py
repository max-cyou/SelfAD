from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import csrf_token_is_valid
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaRepositoryNotFound,
    GiteaUnavailable,
    delete_repository,
    provision_service,
    replace_repository_file,
)
from selfad.models import (
    ParticipantService,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
    User,
)
from selfad.participants import provision_participant_service
from selfad.repository_readmes import issued_service_readme
from selfad.routes.admin_shared import (
    add_service_uniqueness_errors,
    apply_validation_result,
    get_admin_access,
    parse_service_form,
    render_admin,
    run_service_validation,
    validate_service_form,
)
from selfad.scoring import get_scoring_settings
from selfad.settings import get_gitea_settings, get_gitea_webhook_secret


router = APIRouter()


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
            organizer_username=user.gitea_username or "root",
            webhook_secret=get_gitea_webhook_secret(),
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
        expected_generation = service.repository_generation
        try:
            validation = await run_service_validation(service)
        except GiteaRepositoryNotFound as error:
            service.validation_status = ServiceValidationStatus.INVALID
            service.validation_message = str(error)
            service.status = ServiceStatus.DRAFT
            service.validated_source_commit = None
            service.validated_jury_commit = None
            service.container_port = None
            service.healthcheck_path = None
            service.validated_at = datetime.now(timezone.utc)
            session.commit()
            errors["status"] = str(error)
        except GiteaUnavailable as error:
            service.validation_status = ServiceValidationStatus.PENDING
            service.validation_message = str(error)
            service.status = ServiceStatus.DRAFT
            service.validated_source_commit = None
            service.validated_jury_commit = None
            service.container_port = None
            service.healthcheck_path = None
            service.validated_at = datetime.now(timezone.utc)
            session.commit()
            errors["_form"] = str(error)
        else:
            result_is_current = apply_validation_result(
                session,
                service,
                validation,
                expected_generation=expected_generation,
            )
            if not result_is_current:
                errors["status"] = (
                    "A repository changed during validation. Validate it again."
                )
            if result_is_current and not validation.valid:
                service.status = ServiceStatus.DRAFT
                errors["status"] = validation.message
            elif result_is_current and (
                service.runtime_status != ServiceRunStatus.PASSED
                or service.runtime_source_commit != validation.source_commit
                or service.runtime_jury_commit != validation.jury_commit
            ):
                service.status = ServiceStatus.DRAFT
                errors["status"] = (
                    "The automatic runtime check must pass before activation."
                )
            session.commit()
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


@router.post("/admin/services/{service_id}/validate")
async def validate_service(
    service_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    _, redirect = get_admin_access(request, session)
    if redirect:
        return redirect

    service = session.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    expected_generation = service.repository_generation
    try:
        validation = await run_service_validation(service)
    except GiteaRepositoryNotFound as error:
        service.validation_status = ServiceValidationStatus.INVALID
        service.validation_message = str(error)
        service.status = ServiceStatus.DRAFT
        service.validated_source_commit = None
        service.validated_jury_commit = None
        service.container_port = None
        service.healthcheck_path = None
        service.validated_at = datetime.now(timezone.utc)
    except GiteaUnavailable as error:
        service.validation_status = ServiceValidationStatus.PENDING
        service.validation_message = str(error)
        service.validated_source_commit = None
        service.validated_jury_commit = None
        service.container_port = None
        service.healthcheck_path = None
        service.validated_at = datetime.now(timezone.utc)
    else:
        result_is_current = apply_validation_result(
            session,
            service,
            validation,
            expected_generation=expected_generation,
        )
        if result_is_current and not validation.valid:
            service.status = ServiceStatus.DRAFT

    session.commit()
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


@router.post("/admin/services/{service_id}/issue")
async def issue_participant_repositories(
    service_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    service = session.get(Service, service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="Service not found.")
    if service.status != ServiceStatus.ACTIVE or service.runtime_status != ServiceRunStatus.PASSED:
        return render_admin(request, session, user, service_errors={"_form": "Activate the service only after its runtime check passes."}, active_section="services", status_code=422)
    participants = session.scalars(
        select(User).where(User.gitea_username.is_not(None))
    ).all()
    existing_user_ids = set(session.scalars(select(ParticipantService.user_id).where(ParticipantService.service_id == service.id)).all())
    settings = get_gitea_settings()
    scoring = get_scoring_settings(session)
    for participant in participants:
        if participant.id in existing_user_ids:
            continue
        try:
            issued = await run_in_threadpool(
                provision_participant_service,
                settings,
                service=service,
                user=participant,
                attack_requirements=scoring.attack_requirements,
                allow_user_attack_requirements=(
                    scoring.allow_user_attack_requirements
                ),
                webhook_secret=get_gitea_webhook_secret(),
            )
        except (GiteaConflict, GiteaUnavailable, GiteaError) as error:
            return render_admin(request, session, user, service_errors={"_form": str(error)}, active_section="services", status_code=502)
        session.add(issued)
        session.commit()
    if service.container_port is None or not service.repository_path:
        return render_admin(request, session, user, service_errors={"_form": "Validated service runtime metadata is unavailable."}, active_section="services", status_code=502)
    try:
        await run_in_threadpool(
            replace_repository_file,
            settings,
            service.repository_path,
            "README.md",
            content=issued_service_readme(
                service.name,
                service.slug,
                service.container_port,
            ),
            branch=service.default_branch,
            message="Replace author guide with local run instructions",
        )
    except (GiteaConflict, GiteaUnavailable, GiteaError) as error:
        return render_admin(request, session, user, service_errors={"_form": str(error)}, active_section="services", status_code=502)
    return RedirectResponse(url="/admin#services", status_code=303)
