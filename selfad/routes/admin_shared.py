import re
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import get_csrf_token, get_session_user
from selfad.branding import PALETTE_FIELDS, PALETTE_GROUPS, get_branding_context
from selfad.contest import as_utc, contest_state, start_contest_if_due
from selfad.gitea import GiteaRepositoryNotFound, ensure_repository_webhook
from selfad.models import (
    InstanceConfig,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    ServiceStatus,
    ServiceValidationStatus,
    User,
)
from selfad.runner import runner_is_available, runner_mode
from selfad.scoring import (
    PENALTY_MODES,
    REWARD_MODES,
    STDOUT_NOISE_MODES,
    get_scoring_settings,
)
from selfad.service_contract import ServiceContractResult, validate_service_contract
from selfad.settings import get_gitea_settings, get_gitea_webhook_secret
from selfad.web import templates


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
