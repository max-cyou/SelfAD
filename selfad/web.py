from pathlib import Path

from fastapi.templating import Jinja2Templates

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def static_version(filename: str) -> int:
    return (STATIC_DIR / filename).stat().st_mtime_ns


templates.env.globals["static_version"] = static_version
