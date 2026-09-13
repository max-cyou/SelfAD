import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from selfad.models import BrandingSettings, InstanceConfig, PaletteSettings


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class PaletteField:
    label: str
    css_variable: str
    default: str


PALETTE_FIELDS = {
    "page_color": PaletteField("Page", "page", "#F6F7F9"),
    "surface_color": PaletteField("Surface", "surface", "#FFFFFF"),
    "surface_subtle_color": PaletteField(
        "Subtle surface", "surface-subtle", "#FAFBFC"
    ),
    "text_color": PaletteField("Text", "text", "#172033"),
    "text_soft_color": PaletteField("Soft text", "text-soft", "#354052"),
    "muted_color": PaletteField("Muted text", "muted", "#687386"),
    "border_color": PaletteField("Border", "border", "#DFE3E8"),
    "border_hover_color": PaletteField(
        "Border hover", "border-hover", "#B8C0CC"
    ),
    "accent_color": PaletteField("Accent", "accent", "#2563EB"),
    "accent_hover_color": PaletteField(
        "Accent hover", "accent-hover", "#1D4ED8"
    ),
    "accent_contrast_color": PaletteField(
        "On accent", "accent-contrast", "#FFFFFF"
    ),
    "danger_color": PaletteField("Danger", "danger", "#DC2626"),
    "danger_soft_color": PaletteField(
        "Danger surface", "danger-soft", "#FEF2F2"
    ),
}


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

    return {
        "site_name": site_name,
        "brand_title": brand_title,
        "change_title": change_title,
        "remove_standard_logo": remove_standard_logo,
        "show_standard_logo": show_standard_logo,
        "is_configured": is_configured,
        "palette_values": palette_values,
        "palette_style": build_palette_style(palette_values),
    }
