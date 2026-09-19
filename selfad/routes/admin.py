import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import csrf_token_is_valid, get_csrf_token, get_session_user
from selfad.branding import (
    HEX_COLOR_PATTERN,
    PALETTE_FIELDS,
    PALETTE_GROUPS,
    get_branding_context,
)
from selfad.contest import (
    CONTEST_STATES,
    ENDED,
    STARTED,
    as_utc,
    contest_state,
    start_contest_if_due,
)
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaRepositoryNotFound,
    GiteaUnavailable,
    delete_repository,
    delete_gitea_user,
    delete_user_ssh_key,
    ensure_repository_webhook,
    create_gitea_user,
    gitea_username_exists,
    add_user_ssh_key,
    provision_service,
    update_gitea_user,
)
from selfad.models import (
    BrandingSettings,
    InstanceConfig,
    PaletteSettings,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
    User,
)
from selfad.participants import provision_participant_service
from selfad.runner import runner_is_available, runner_mode
from selfad.scoring import (
    PENALTY_MODES,
    REWARD_MODES,
    STDOUT_NOISE_MODES,
    get_scoring_settings,
)
from selfad.security import hash_password
from selfad.service_contract import (
    ServiceContractResult,
    validate_service_contract,
)
from selfad.settings import (
    get_gitea_root_password,
    get_gitea_settings,
    get_gitea_webhook_secret,
    set_gitea_root_password,
)
from selfad.web import templates


router = APIRouter()

SERVICE_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
USERS_PAGE_SIZE = 20
MAX_ATTACK_REQUIREMENTS_BYTES = 64 * 1024

DEFAULT_SERVICE_FORM = {
    "name": "",
    "slug": "",
    "description": "",
    "default_branch": "main",
    "status": ServiceStatus.DRAFT.value,
    "ssh_public_key": "",
}

DEFAULT_PARTICIPANT_FORM = {
    "username": "",
    "email": "",
    "ssh_public_key": "",
    "role": "user",
}

SCORING_INTEGER_FIELDS = {
    "attack_max_points": (1, 1_000_000),
    "attack_points_per_flag": (0, 1_000_000),
    "defense_max_points": (1, 1_000_000),
    "defense_points_lost_per_flag": (0, 1_000_000),
    "attack_free_failures": (0, 10_000),
    "defense_free_failures": (0, 10_000),
}


def parse_scoring_form(form) -> tuple[dict[str, object], dict[str, str]]:
    values: dict[str, object] = {
        "attack_reward_mode": str(
            form.get("attack_reward_mode", "coverage")
        ).strip(),
        "defense_reward_mode": str(
            form.get("defense_reward_mode", "per_flag")
        ).strip(),
        "penalty_mode": str(form.get("penalty_mode", "percent")).strip(),
        "penalize_check_errors": form.get("penalize_check_errors") == "on",
        "stdout_noise_mode": str(
            form.get("stdout_noise_mode", "ignore")
        ).strip(),
        "attack_requirements": str(form.get("attack_requirements", "")),
        "allow_user_attack_requirements": (
            form.get("allow_user_attack_requirements") == "on"
        ),
    }
    errors: dict[str, str] = {}
    if values["attack_reward_mode"] not in REWARD_MODES:
        errors["attack_reward_mode"] = "Choose a valid reward mode."
    if values["defense_reward_mode"] not in REWARD_MODES:
        errors["defense_reward_mode"] = "Choose a valid reward mode."
    if values["penalty_mode"] not in PENALTY_MODES:
        errors["penalty_mode"] = "Choose a valid penalty mode."
    if values["stdout_noise_mode"] not in STDOUT_NOISE_MODES:
        errors["stdout_noise_mode"] = "Choose a valid stdout rule."

    for name, (minimum, maximum) in SCORING_INTEGER_FIELDS.items():
        raw_value = str(form.get(name, "")).strip()
        values[name] = raw_value
        try:
            parsed = int(raw_value)
            if not minimum <= parsed <= maximum:
                raise ValueError
        except ValueError:
            errors[name] = f"Use a whole number from {minimum} to {maximum}."
        else:
            values[name] = parsed

    penalty_maximum = (
        100
        if values["penalty_mode"] in {"percent", "compound_percent"}
        else 1_000_000
    )
    for name in ("attack_penalty_value", "defense_penalty_value"):
        raw_value = str(form.get(name, "")).strip()
        values[name] = raw_value
        try:
            parsed = float(raw_value)
            if not 0 <= parsed <= penalty_maximum:
                raise ValueError
        except ValueError:
            errors[name] = f"Use a number from 0 to {penalty_maximum}."
        else:
            values[name] = parsed
    raw_stdout_noise_penalty = str(
        form.get("stdout_noise_penalty_percent", "")
    ).strip()
    values["stdout_noise_penalty_percent"] = raw_stdout_noise_penalty
    try:
        stdout_noise_penalty = float(raw_stdout_noise_penalty)
        if not 0 <= stdout_noise_penalty <= 100:
            raise ValueError
    except ValueError:
        errors["stdout_noise_penalty_percent"] = "Use a percentage from 0 to 100."
    else:
        values["stdout_noise_penalty_percent"] = stdout_noise_penalty
    requirements = str(values["attack_requirements"])
    if "\x00" in requirements:
        errors["attack_requirements"] = "Requirements must not contain null bytes."
    elif len(requirements.encode("utf-8")) > MAX_ATTACK_REQUIREMENTS_BYTES:
        errors["attack_requirements"] = "Use no more than 64 KB of requirements."
    return values, errors


def _requested_users_page(request: Request) -> int:
    try:
        return max(1, int(request.query_params.get("users_page", "1")))
    except (TypeError, ValueError):
        return 1


def _requested_users_search(request: Request) -> str:
    raw_search = str(request.query_params.get("users_search", "")).strip()
    return re.sub(r"[^A-Za-z0-9_.-]", "", raw_search)[:32]


def _pagination_items(current_page: int, total_pages: int) -> list[int | None]:
    if total_pages <= 7:
        return list(range(1, total_pages + 1))

    visible_pages = sorted(
        page
        for page in {1, total_pages, current_page - 1, current_page, current_page + 1}
        if 1 <= page <= total_pages
    )
    items: list[int | None] = []
    previous = 0
    for page in visible_pages:
        if page - previous > 1:
            items.append(None)
        items.append(page)
        previous = page
    return items


def _users_admin_url(request: Request) -> str:
    page = _requested_users_page(request)
    search = _requested_users_search(request)
    search_query = f"&users_search={search}" if search else ""
    return f"/admin?users_page={page}{search_query}#users"


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
    participant_errors: dict[str, str] | None = None,
    participant_form: dict[str, str] | None = None,
    user_edit_id: int | None = None,
    general_errors: dict[str, str] | None = None,
    general_form: dict[str, object] | None = None,
    active_section: str = "general",
    appearance_mode: str | None = None,
    status_code: int = 200,
):
    config = session.get(InstanceConfig, 1)
    if start_contest_if_due(config):
        session.commit()
    branding = get_branding_context(session)
    scoring = get_scoring_settings(session)
    scheduled_start = config.contest_starts_at if config else None
    scheduled_end = config.contest_ends_at if config else None
    scheduled_start_utc = (
        as_utc(scheduled_start).isoformat().replace("+00:00", "Z")
        if scheduled_start
        else ""
    )
    scheduled_end_utc = (
        as_utc(scheduled_end).isoformat().replace("+00:00", "Z")
        if scheduled_end
        else ""
    )
    general_values: dict[str, object] = {
        "contest_state": contest_state(config),
        "contest_starts_at_local": "",
        "contest_starts_at_utc": scheduled_start_utc,
        "contest_ends_at_local": "",
        "contest_ends_at_utc": scheduled_end_utc,
        "registration_enabled": branding["registration_enabled"],
        "registration_invite_only": branding["registration_invite_only"],
        "attack_reward_mode": scoring.attack_reward_mode,
        "attack_max_points": scoring.attack_max_points,
        "attack_points_per_flag": scoring.attack_points_per_flag,
        "defense_reward_mode": scoring.defense_reward_mode,
        "defense_max_points": scoring.defense_max_points,
        "defense_points_lost_per_flag": scoring.defense_points_lost_per_flag,
        "penalty_mode": scoring.penalty_mode,
        "attack_penalty_value": scoring.attack_penalty_value,
        "defense_penalty_value": scoring.defense_penalty_value,
        "attack_free_failures": scoring.attack_free_failures,
        "defense_free_failures": scoring.defense_free_failures,
        "penalize_check_errors": scoring.penalize_check_errors,
        "stdout_noise_mode": scoring.stdout_noise_mode,
        "stdout_noise_penalty_percent": scoring.stdout_noise_penalty_percent,
        "attack_requirements": scoring.attack_requirements,
        "allow_user_attack_requirements": (
            scoring.allow_user_attack_requirements
        ),
    }
    if general_form:
        general_values.update(general_form)
    appearance_values = {
        "site_name": branding["site_name"],
        "change_title": branding["change_title"],
        "remove_standard_logo": branding["remove_standard_logo"],
        "not_started_homepage_html": branding["not_started_homepage_html"],
        "started_homepage_html": branding["started_homepage_html"],
        "ended_homepage_html": branding["ended_homepage_html"],
        **branding["palette_values"],
    }
    if form_values:
        appearance_values.update(form_values)

    services = session.scalars(
        select(Service).order_by(Service.id)
    ).all()
    users_search = _requested_users_search(request)
    users_query = select(User)
    if users_search:
        users_query = users_query.where(User.username.ilike(f"%{users_search}%"))
    users_total = session.scalar(
        select(func.count()).select_from(users_query.subquery())
    ) or 0
    users_pages = max(1, (users_total + USERS_PAGE_SIZE - 1) // USERS_PAGE_SIZE)
    users_page = min(_requested_users_page(request), users_pages)
    users = session.scalars(
        users_query
        .order_by(User.username, User.id)
        .offset((users_page - 1) * USERS_PAGE_SIZE)
        .limit(USERS_PAGE_SIZE)
    ).all()
    visible_user_ids = [listed_user.id for listed_user in users]
    points_by_user = {listed_user.id: 0 for listed_user in users}
    if visible_user_ids:
        points_by_user.update(
            dict(
                session.execute(
                    select(
                        ParticipantService.user_id,
                        func.sum(
                            ParticipantService.attack_score
                            + ParticipantService.defense_score
                        ),
                    )
                    .where(ParticipantService.user_id.in_(visible_user_ids))
                    .group_by(ParticipantService.user_id)
                ).all()
            )
        )
    issued_counts = dict(
        session.execute(
            select(ParticipantService.service_id, func.count())
            .group_by(ParticipantService.service_id)
        ).all()
    )
    gitea_settings = get_gitea_settings()
    pending_by_repository = dict(
        session.execute(
            select(RepositoryEvent.repository_path, func.count())
            .where(RepositoryEvent.status == RepositoryEventStatus.PENDING)
            .group_by(RepositoryEvent.repository_path)
        ).all()
    )
    pending_pushes = {
        service.id: pending_by_repository.get(service.repository_path, 0)
        + pending_by_repository.get(service.jury_repository_path, 0)
        for service in services
    }

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
            "palette_groups": PALETTE_GROUPS,
            "csrf_token": get_csrf_token(request),
            "active_section": active_section,
            "appearance_mode": (
                appearance_mode
                if appearance_mode in {"identity", "palette", "templates"}
                else "identity"
            ),
            "services": services,
            "service_errors": service_errors or {},
            "service_form": service_form or DEFAULT_SERVICE_FORM,
            "service_edit_id": service_edit_id,
            "users": users,
            "users_total": users_total,
            "users_search": users_search,
            "users_page": users_page,
            "users_pages": users_pages,
            "users_page_items": _pagination_items(users_page, users_pages),
            "points_by_user": points_by_user,
            "issued_counts": issued_counts,
            "participant_errors": participant_errors or {},
            "participant_form": participant_form or DEFAULT_PARTICIPANT_FORM,
            "user_edit_id": user_edit_id,
            "general_errors": general_errors or {},
            "general_form": general_values,
            "ssh_key_required": not bool(user.ssh_public_key),
            "gitea_configured": gitea_settings.configured,
            "gitea_public_url": gitea_settings.public_url,
            "pending_pushes": pending_pushes,
            "runner_mode": runner_mode(),
            "runner_ready": runner_is_available(),
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


def validate_participant_form(
    values: dict[str, str],
    password: str,
    *,
    editing: bool = False,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", values["username"]):
        errors["username"] = "Use 3–32 letters, numbers, dots, dashes or underscores."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"]):
        errors["email"] = "Enter a valid email address."
    if (not editing or password) and not 10 <= len(password) <= 128:
        errors["password"] = "Use between 10 and 128 characters."
    if values.get("role") not in {"user", "admin"}:
        errors["role"] = "Select user or admin."
    if values["ssh_public_key"] and not values["ssh_public_key"].startswith(("ssh-", "ecdsa-", "sk-ssh-", "sk-ecdsa-")):
        errors["ssh_public_key"] = "Add the participant SSH public key."
    elif not editing and not values["ssh_public_key"]:
        errors["ssh_public_key"] = "Add the participant SSH public key."
    return errors


async def run_service_validation(service: Service) -> ServiceContractResult:
    settings = get_gitea_settings()
    if (
        not settings.configured
        or service.repository_path is None
        or service.jury_repository_path is None
    ):
        raise GiteaRepositoryNotFound(
            "The service and jury repositories must be provisioned."
        )

    webhook_secret = get_gitea_webhook_secret()
    for repository_path in (
        service.repository_path,
        service.jury_repository_path,
    ):
        await run_in_threadpool(
            ensure_repository_webhook,
            settings,
            repository_path,
            secret=webhook_secret,
            branch_filter=service.default_branch,
        )

    return await run_in_threadpool(
        validate_service_contract,
        settings,
        repository_path=service.repository_path,
        jury_repository_path=service.jury_repository_path,
        default_branch=service.default_branch,
    )


def apply_validation_result(
    session: Session,
    service: Service,
    result: ServiceContractResult,
    *,
    expected_generation: int,
) -> bool:
    validation_status = (
        ServiceValidationStatus.VALID
        if result.valid
        else ServiceValidationStatus.INVALID
    )
    execution = session.execute(
        update(Service)
        .where(
            Service.id == service.id,
            Service.repository_generation == expected_generation,
        )
        .values(
            validation_status=validation_status,
            validation_message=result.message,
            validated_source_commit=result.source_commit,
            validated_jury_commit=result.jury_commit,
            container_port=result.container_port,
            healthcheck_path=result.healthcheck_path,
            validated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    if execution.rowcount != 1:
        session.rollback()
        session.refresh(service)
        return False

    session.refresh(service)
    return True


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
    for participant in participants:
        if participant.id in existing_user_ids:
            continue
        try:
            issued = await run_in_threadpool(provision_participant_service, settings, service=service, user=participant, webhook_secret=get_gitea_webhook_secret())
        except (GiteaConflict, GiteaUnavailable, GiteaError) as error:
            return render_admin(request, session, user, service_errors={"_form": str(error)}, active_section="services", status_code=502)
        session.add(issued)
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


@router.post("/admin/participants")
async def create_participant(
    request: Request,
    session: Session = Depends(get_session),
):
    _, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    values = {
        "username": str(form.get("username", "")).strip(),
        "email": str(form.get("email", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
        "role": str(form.get("role", "user")).strip().lower(),
    }
    password = str(form.get("password", ""))
    errors = validate_participant_form(values, password)
    if session.scalar(select(User.id).where(User.username == values["username"])):
        errors["username"] = "This participant already exists."
    if session.scalar(select(User.id).where(User.email == values["email"])):
        errors["email"] = "This email is already in use."
    settings = get_gitea_settings()
    if not errors:
        try:
            taken_in_gitea = await run_in_threadpool(
                gitea_username_exists,
                settings,
                username=values["username"],
            )
        except GiteaUnavailable as error:
            return render_admin(request, session, get_session_user(request, session), participant_errors={"_form": str(error)}, participant_form=values, active_section="users", status_code=502)
        if taken_in_gitea:
            errors["username"] = "This username is already in use."
    if errors:
        return render_admin(request, session, get_session_user(request, session), participant_errors=errors, participant_form=values, active_section="users", status_code=422)

    gitea_user = None
    try:
        gitea_user = await run_in_threadpool(create_gitea_user, settings, username=values["username"], email=values["email"], password=password)
        ssh_key_id = await run_in_threadpool(add_user_ssh_key, settings, username=gitea_user.username, public_key=values["ssh_public_key"])
    except GiteaConflict as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(delete_gitea_user, settings, username=gitea_user.username)
            except GiteaError:
                pass
        return render_admin(request, session, get_session_user(request, session), participant_errors={"username" if gitea_user is None else "ssh_public_key": str(error)}, participant_form=values, active_section="users", status_code=422)
    except GiteaUnavailable as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(delete_gitea_user, settings, username=gitea_user.username)
            except GiteaError:
                pass
        return render_admin(request, session, get_session_user(request, session), participant_errors={"_form": str(error)}, participant_form=values, active_section="users", status_code=502)

    participant = User(username=values["username"], email=values["email"], password_hash=hash_password(password), is_admin=values["role"] == "admin", ssh_public_key=values["ssh_public_key"], git_ssh_key_id=ssh_key_id, gitea_user_id=gitea_user.id, gitea_username=gitea_user.username)
    session.add(participant)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)


@router.post("/admin/users/{user_id}", response_class=HTMLResponse)
async def update_user(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    values = {
        "username": target.username,
        "email": str(form.get("email", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
        "role": str(form.get("role", "user")).strip().lower(),
    }
    password = str(form.get("password", ""))
    errors = validate_participant_form(values, password, editing=True)
    duplicate_email = session.scalar(
        select(User.id).where(User.email == values["email"], User.id != target.id)
    )
    if duplicate_email is not None:
        errors["email"] = "This email is already in use."
    if target.id == current_user.id and values["role"] != "admin":
        errors["role"] = "You cannot remove your own admin access."
    if errors:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors=errors,
            participant_form=values,
            user_edit_id=target.id,
            active_section="users",
            status_code=422,
        )

    settings = get_gitea_settings()
    if target.gitea_username:
        try:
            await run_in_threadpool(
                update_gitea_user,
                settings,
                username=target.gitea_username,
                email=values["email"],
                password=password or None,
            )
            if target.gitea_username == "root" and password:
                await run_in_threadpool(set_gitea_root_password, password)
        except (GiteaConflict, GiteaUnavailable, OSError) as error:
            return render_admin(
                request,
                session,
                current_user,
                participant_errors={"_form": str(error)},
                participant_form=values,
                user_edit_id=target.id,
                active_section="users",
                status_code=502,
            )
    if values["ssh_public_key"] and target.gitea_username:
        if values["ssh_public_key"] != target.ssh_public_key:
            try:
                if target.git_ssh_key_id:
                    await run_in_threadpool(
                        delete_user_ssh_key,
                        settings,
                        username=target.gitea_username,
                        key_id=target.git_ssh_key_id,
                    )
                target.git_ssh_key_id = await run_in_threadpool(
                    add_user_ssh_key,
                    settings,
                    username=target.gitea_username,
                    public_key=values["ssh_public_key"],
                )
            except (GiteaConflict, GiteaUnavailable) as error:
                return render_admin(
                    request,
                    session,
                    current_user,
                    participant_errors={"_form": str(error)},
                    participant_form=values,
                    user_edit_id=target.id,
                    active_section="users",
                    status_code=502,
                )
            target.ssh_public_key = values["ssh_public_key"]

    target.email = values["email"]
    target.is_admin = values["role"] == "admin"
    if password:
        target.password_hash = hash_password(password)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    if target.id == current_user.id:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors={"_form": "You cannot delete the account you are using."},
            active_section="users",
            status_code=422,
        )

    assignments = session.scalars(
        select(ParticipantService).where(ParticipantService.user_id == target.id)
    ).all()
    settings = get_gitea_settings()
    try:
        for assignment in assignments:
            for repository_path in (
                assignment.attack_repository_path,
                assignment.defense_repository_path,
            ):
                await run_in_threadpool(delete_repository, settings, repository_path)
        if target.gitea_username and target.gitea_username != "root":
            await run_in_threadpool(
                delete_gitea_user,
                settings,
                username=target.gitea_username,
            )
    except GiteaError as error:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors={"_form": str(error)},
            active_section="users",
            status_code=502,
        )

    for assignment in assignments:
        session.delete(assignment)
    session.delete(target)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)


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

    current_appearance = get_branding_context(session)
    site_name = str(form.get("site_name", current_appearance["site_name"])).strip()
    appearance_mode = str(form.get("appearance_mode", "identity"))
    if appearance_mode not in {"identity", "palette", "templates"}:
        appearance_mode = "identity"
    not_started_homepage_html = str(
        form.get(
            "not_started_homepage_html",
            current_appearance["not_started_homepage_html"],
        )
    ).strip()
    started_homepage_html = str(
        form.get(
            "started_homepage_html",
            current_appearance["started_homepage_html"],
        )
    ).strip()
    ended_homepage_html = str(
        form.get(
            "ended_homepage_html",
            current_appearance["ended_homepage_html"],
        )
    ).strip()
    palette_values = {
        name: str(
            form.get(name, current_appearance["palette_values"][name])
        ).strip().upper()
        for name in PALETTE_FIELDS
    }
    errors: dict[str, str] = {}
    if not 2 <= len(site_name) <= 120:
        errors["site_name"] = "Use between 2 and 120 characters."
    if appearance_mode == "templates":
        for field_name, value in (
            ("not_started_homepage_html", not_started_homepage_html),
            ("started_homepage_html", started_homepage_html),
            ("ended_homepage_html", ended_homepage_html),
        ):
            if not value:
                errors[field_name] = "Template HTML cannot be empty."
            elif len(value) > 20_000:
                errors[field_name] = "Use no more than 20,000 characters."
    elif appearance_mode == "palette":
        for name, value in palette_values.items():
            if not HEX_COLOR_PATTERN.fullmatch(value):
                errors[name] = "Use #RRGGBB."

    form_values: dict[str, object] = {
        "site_name": site_name,
        "change_title": form.get("change_title") == "on",
        "remove_standard_logo": form.get("remove_standard_logo") == "on",
        "not_started_homepage_html": not_started_homepage_html,
        "started_homepage_html": started_homepage_html,
        "ended_homepage_html": ended_homepage_html,
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
            appearance_mode=appearance_mode,
            status_code=422,
        )

    config = session.get(InstanceConfig, 1)
    branding = session.get(BrandingSettings, 1)
    palette = session.get(PaletteSettings, 1)
    if config is None or branding is None:
        return RedirectResponse(url="/setup", status_code=303)
    if palette is None and appearance_mode == "palette":
        palette = PaletteSettings(id=1, **palette_values)

    config.site_name = site_name
    branding.change_title = bool(form_values["change_title"])
    branding.remove_standard_logo = bool(
        form_values["remove_standard_logo"]
    )
    if appearance_mode == "templates":
        branding.homepage_html = not_started_homepage_html
        branding.started_homepage_html = started_homepage_html
        branding.ended_homepage_html = ended_homepage_html
    elif appearance_mode == "palette":
        for name, value in palette_values.items():
            setattr(palette, name, value)
        session.add(palette)
    session.add_all([config, branding])
    session.commit()

    return RedirectResponse(url="/admin?saved=true#appearance", status_code=303)


@router.post("/admin/appearance/reset", response_class=HTMLResponse)
async def reset_appearance(
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    config = session.get(InstanceConfig, 1)
    branding = session.get(BrandingSettings, 1)
    if config is None or branding is None:
        return RedirectResponse(url="/setup", status_code=303)
    palette = session.get(PaletteSettings, 1)
    if palette is None:
        palette = PaletteSettings(
            id=1,
            **{name: field.default for name, field in PALETTE_FIELDS.items()},
        )
    else:
        for name, field in PALETTE_FIELDS.items():
            setattr(palette, name, field.default)

    config.site_name = "SelfAD"
    branding.change_title = False
    branding.remove_standard_logo = False
    branding.homepage_html = ""
    branding.started_homepage_html = ""
    branding.ended_homepage_html = ""
    session.add(palette)
    session.commit()
    return RedirectResponse(url="/admin?saved=true#appearance", status_code=303)


@router.post("/admin/registration", response_class=HTMLResponse)
async def update_registration(
    request: Request,
    session: Session = Depends(get_session),
):
    user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)

    config = session.get(InstanceConfig, 1)
    if config is None:
        return RedirectResponse(url="/setup", status_code=303)

    registration_enabled = form.get("registration_enabled") == "on"
    invite_only = form.get("registration_invite_only") == "on"
    invite_code = str(form.get("registration_invite_code", "")).strip()
    requested_contest_state = str(
        form.get("contest_state", "not_started")
    ).strip()
    contest_starts_at_local = str(
        form.get("contest_starts_at_local", "")
    ).strip()
    contest_ends_at_local = str(form.get("contest_ends_at_local", "")).strip()
    browser_timezone = str(form.get("browser_timezone", "UTC")).strip()
    scoring_values, scoring_errors = parse_scoring_form(form)
    contest_starts_at: datetime | None = None
    contest_ends_at: datetime | None = None
    errors: dict[str, str] = {}
    errors.update(scoring_errors)
    if requested_contest_state not in CONTEST_STATES:
        errors["contest_state"] = "Choose a valid contest state."
    if contest_starts_at_local:
        try:
            local_start = datetime.fromisoformat(contest_starts_at_local)
            if local_start.tzinfo is not None:
                raise ValueError
            try:
                start_timezone = ZoneInfo(browser_timezone)
            except (ZoneInfoNotFoundError, ValueError):
                start_timezone = timezone.utc
            contest_starts_at = local_start.replace(
                tzinfo=start_timezone
            ).astimezone(timezone.utc)
        except ValueError:
            errors["contest_starts_at_local"] = "Use a valid date and time."
    if contest_ends_at_local:
        try:
            local_end = datetime.fromisoformat(contest_ends_at_local)
            if local_end.tzinfo is not None:
                raise ValueError
            try:
                end_timezone = ZoneInfo(browser_timezone)
            except (ZoneInfoNotFoundError, ValueError):
                end_timezone = timezone.utc
            contest_ends_at = local_end.replace(
                tzinfo=end_timezone
            ).astimezone(timezone.utc)
        except ValueError:
            errors["contest_ends_at_local"] = "Use a valid date and time."
    if contest_starts_at and contest_ends_at and contest_ends_at <= contest_starts_at:
        errors["contest_ends_at_local"] = "Automatic stop must be after automatic start."
    if invite_code and not 4 <= len(invite_code) <= 128:
        errors["registration_invite_code"] = "Use between 4 and 128 characters."
    if invite_only and not invite_code and not config.registration_invite_code_hash:
        errors["registration_invite_code"] = "Set an invite code first."
    if errors:
        return render_admin(
            request,
            session,
            user,
            general_errors=errors,
            general_form={
                "contest_state": requested_contest_state,
                "contest_starts_at_local": contest_starts_at_local,
                "contest_starts_at_utc": "",
                "contest_ends_at_local": contest_ends_at_local,
                "contest_ends_at_utc": "",
                "registration_enabled": registration_enabled,
                "registration_invite_only": invite_only,
                **scoring_values,
            },
            active_section="general",
            status_code=422,
        )

    config.contest_started = requested_contest_state in {STARTED, ENDED}
    config.contest_ended = requested_contest_state == ENDED
    config.contest_starts_at = contest_starts_at
    config.contest_ends_at = contest_ends_at
    config.registration_enabled = registration_enabled
    config.registration_invite_only = invite_only
    scoring = get_scoring_settings(session)
    scoring.attack_reward_mode = str(scoring_values["attack_reward_mode"])
    scoring.attack_max_points = int(scoring_values["attack_max_points"])
    scoring.attack_points_per_flag = int(
        scoring_values["attack_points_per_flag"]
    )
    scoring.defense_reward_mode = str(scoring_values["defense_reward_mode"])
    scoring.defense_max_points = int(scoring_values["defense_max_points"])
    scoring.defense_points_lost_per_flag = int(
        scoring_values["defense_points_lost_per_flag"]
    )
    scoring.penalty_mode = str(scoring_values["penalty_mode"])
    scoring.attack_penalty_value = float(
        scoring_values["attack_penalty_value"]
    )
    scoring.defense_penalty_value = float(
        scoring_values["defense_penalty_value"]
    )
    scoring.attack_free_failures = int(
        scoring_values["attack_free_failures"]
    )
    scoring.defense_free_failures = int(
        scoring_values["defense_free_failures"]
    )
    scoring.penalize_check_errors = bool(
        scoring_values["penalize_check_errors"]
    )
    scoring.stdout_noise_mode = str(scoring_values["stdout_noise_mode"])
    scoring.stdout_noise_penalty_percent = float(
        scoring_values["stdout_noise_penalty_percent"]
    )
    # The legacy field is retained only so old application versions can read
    # the database safely; the mode above is authoritative.
    scoring.penalize_stdout_noise = (
        scoring.stdout_noise_mode == "unsuccessful"
    )
    scoring.attack_requirements = str(scoring_values["attack_requirements"])
    scoring.allow_user_attack_requirements = bool(
        scoring_values["allow_user_attack_requirements"]
    )
    if invite_code:
        config.registration_invite_code_hash = hash_password(invite_code)
    session.commit()
    return RedirectResponse(url="/admin#general", status_code=303)
