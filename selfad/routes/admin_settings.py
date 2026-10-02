from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid
from selfad.branding import HEX_COLOR_PATTERN, PALETTE_FIELDS, get_branding_context
from selfad.contest import CONTEST_STATES, ENDED, STARTED
from selfad.database import get_session
from selfad.models import BrandingSettings, InstanceConfig, PaletteSettings
from selfad.routes.admin_shared import (
    get_admin_access,
    parse_scoring_form,
    render_admin,
)
from selfad.scoring import get_scoring_settings
from selfad.security import hash_password


router = APIRouter()


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
