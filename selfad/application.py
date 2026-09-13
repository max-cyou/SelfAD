from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from selfad.database import get_session, initialize_database
from selfad.models import InstanceConfig


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SelfAD", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"title": app.title},
        )

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(
        request: Request,
        session: Session = Depends(get_session),
    ):
        config = session.get(InstanceConfig, 1)

        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={
                "title": "Configure SelfAD",
                "site_name": config.site_name if config else "SelfAD",
                "admin_username": "",
                "admin_email": "",
                "setup_complete": bool(config and config.setup_complete),
                "errors": {},
            },
        )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
