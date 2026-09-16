import re
from html import escape
from dataclasses import dataclass

from sqlalchemy.orm import Session

from selfad.contest import ENDED, STARTED, contest_state
from selfad.models import BrandingSettings, InstanceConfig, PaletteSettings


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class PaletteField:
    label: str
    css_variable: str
    default: str
    group: str


PALETTE_FIELDS = {
    "page_color": PaletteField("Page background", "page", "#F6F7F9", "Base"),
    "surface_color": PaletteField("Blocks", "surface", "#FFFFFF", "Base"),
    "surface_subtle_color": PaletteField(
        "Subtle blocks", "surface-subtle", "#FAFBFC", "Base"
    ),
    "text_color": PaletteField("Primary text", "text", "#172033", "Text & borders"),
    "text_soft_color": PaletteField("Secondary text", "text-soft", "#354052", "Text & borders"),
    "muted_color": PaletteField("Muted text", "muted", "#687386", "Text & borders"),
    "border_color": PaletteField("Borders", "border", "#DFE3E8", "Text & borders"),
    "border_hover_color": PaletteField(
        "Hovered borders", "border-hover", "#B8C0CC", "Text & borders"
    ),
    "focus_color": PaletteField("Focus ring", "focus", "#2563EB", "Text & borders"),
    "accent_color": PaletteField("Links", "accent", "#2563EB", "Links & actions"),
    "accent_hover_color": PaletteField(
        "Hovered links", "accent-hover", "#1D4ED8", "Links & actions"
    ),
    "accent_contrast_color": PaletteField(
        "Text on accent", "accent-contrast", "#FFFFFF", "Links & actions"
    ),
    "button_color": PaletteField("Primary button", "button", "#172033", "Links & actions"),
    "button_hover_color": PaletteField("Button hover", "button-hover", "#354052", "Links & actions"),
    "button_text_color": PaletteField("Button text", "button-text", "#FFFFFF", "Links & actions"),
    "success_color": PaletteField("Success", "success", "#287455", "Statuses"),
    "success_soft_color": PaletteField("Success background", "success-soft", "#EEF6F1", "Statuses"),
    "warning_color": PaletteField("Warning", "warning", "#9A6700", "Statuses"),
    "warning_soft_color": PaletteField("Warning background", "warning-soft", "#FFF8C5", "Statuses"),
    "danger_color": PaletteField("Error", "danger", "#DC2626", "Statuses"),
    "danger_soft_color": PaletteField(
        "Error background", "danger-soft", "#FEF2F2", "Statuses"
    ),
    "input_color": PaletteField("Input background", "input", "#FFFFFF", "Forms & tables"),
    "input_disabled_color": PaletteField("Disabled input", "input-disabled", "#FAFBFC", "Forms & tables"),
    "table_heading_color": PaletteField("Table headings", "table-heading", "#FAFBFC", "Forms & tables"),
    "table_hover_color": PaletteField("Hovered row", "table-hover", "#FAFBFC", "Forms & tables"),
    "table_selected_color": PaletteField("Selected row", "table-selected", "#F3F6FA", "Forms & tables"),
    "header_color": PaletteField("Header background", "header", "#FFFFFF", "Header"),
    "header_text_color": PaletteField("Tournament title", "header-text", "#111827", "Header"),
    "header_link_color": PaletteField("Navigation links", "header-link", "#6B7280", "Header"),
    "header_link_hover_color": PaletteField("Active navigation", "header-link-hover", "#111827", "Header"),
    "home_color": PaletteField("Home background", "home", "#FFFFFF", "Home & footer"),
    "home_title_color": PaletteField("Home title", "home-title", "#111827", "Home & footer"),
    "home_text_color": PaletteField("Home status", "home-text", "#6B7280", "Home & footer"),
    "home_link_color": PaletteField("Home links", "home-link", "#4B5563", "Home & footer"),
    "home_link_hover_color": PaletteField("Home link hover", "home-link-hover", "#111827", "Home & footer"),
    "footer_text_color": PaletteField("Footer text", "footer-text", "#9CA3AF", "Home & footer"),
    "footer_hover_color": PaletteField("Footer link hover", "footer-hover", "#6B7280", "Home & footer"),
}

PALETTE_GROUPS = tuple(
    (
        group,
        tuple(name for name, field in PALETTE_FIELDS.items() if field.group == group),
    )
    for group in dict.fromkeys(field.group for field in PALETTE_FIELDS.values())
)

HOME_ARROW_SVG = (
    '<svg viewBox="0 0 12 12" aria-hidden="true">'
    '<path d="M2 6h7M6.5 2.5 10 6 6.5 9.5"/></svg>'
)


def default_not_started_homepage_html(site_name: str) -> str:
    safe_name = escape(site_name)
    return (
        f"<h1>{safe_name}</h1>\n"
        "<p>Has not started yet!</p>\n"
        '<nav class="home-links" aria-label="Contest links">\n'
        f'  <a href="/login"><span>Login / register</span>{HOME_ARROW_SVG}</a>\n'
        "</nav>"
    )


def default_started_homepage_html(site_name: str) -> str:
    safe_name = escape(site_name)
    return (
        f"<h1>{safe_name}</h1>\n"
        "<p>Contest is started</p>\n"
        '<nav class="home-links" aria-label="Contest links">\n'
        f'  <a href="/services"><span>Services</span>{HOME_ARROW_SVG}</a>\n'
        f'  <a href="/scoreboard"><span>Scoreboard</span>{HOME_ARROW_SVG}</a>\n'
        "</nav>"
    )


def default_ended_homepage_html(site_name: str) -> str:
    safe_name = escape(site_name)
    return (
        f"<h1>{safe_name}</h1>\n"
        "<p>Contest has ended</p>\n"
        '<nav class="home-links" aria-label="Contest links">\n'
        f'  <a href="/scoreboard"><span>Scoreboard</span>{HOME_ARROW_SVG}</a>\n'
        "</nav>"
    )


def get_palette_values(palette: PaletteSettings | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, field in PALETTE_FIELDS.items():
        value = getattr(palette, name, field.default) if palette else field.default
        values[name] = value if HEX_COLOR_PATTERN.fullmatch(value) else field.default
    return values


def build_palette_style(values: dict[str, str]) -> str:
    return "; ".join(
        f"--{field.css_variable}: {values[name]}"
        for name, field in PALETTE_FIELDS.items()
    )


def get_branding_context(session: Session) -> dict[str, object]:
    config = session.get(InstanceConfig, 1)
    branding = session.get(BrandingSettings, 1)
    palette = session.get(PaletteSettings, 1)
    is_configured = bool(config and config.setup_complete)
    registration_enabled = bool(
        is_configured and config and config.registration_enabled
    )
    registration_invite_only = bool(
        config and config.registration_invite_only
    )
    change_title = bool(
        is_configured and branding and branding.change_title
    )
    remove_standard_logo = bool(
        is_configured and branding and branding.remove_standard_logo
    )
    site_name = config.site_name if is_configured else "SelfAD"
    brand_title = site_name if change_title else "SelfAD"
    show_standard_logo = not remove_standard_logo
    palette_values = get_palette_values(palette)
    stored_not_started_html = branding.homepage_html if branding else ""
    stored_started_html = branding.started_homepage_html if branding else ""
    stored_ended_html = branding.ended_homepage_html if branding else ""
    not_started_homepage_html = (
        stored_not_started_html or default_not_started_homepage_html(site_name)
    )
    started_homepage_html = (
        stored_started_html or default_started_homepage_html(site_name)
    )
    ended_homepage_html = (
        stored_ended_html or default_ended_homepage_html(site_name)
    )
    current_contest_state = contest_state(config)

    return {
        "site_name": site_name,
        "brand_title": brand_title,
        "change_title": change_title,
        "remove_standard_logo": remove_standard_logo,
        "show_standard_logo": show_standard_logo,
        "is_configured": is_configured,
        "registration_enabled": registration_enabled,
        "registration_invite_only": registration_invite_only,
        "registration_invite_code_configured": bool(
            config and config.registration_invite_code_hash
        ),
        "contest_state": current_contest_state,
        "contest_started": current_contest_state == STARTED,
        "contest_ended": current_contest_state == ENDED,
        "not_started_homepage_html": not_started_homepage_html,
        "started_homepage_html": started_homepage_html,
        "ended_homepage_html": ended_homepage_html,
        "palette_values": palette_values,
        "palette_style": build_palette_style(palette_values),
    }
