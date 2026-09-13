from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from selfad.database import initialize_database
from selfad.routes import admin, authentication, health, home, setup
from selfad.settings import get_session_secret, use_secure_cookies
from selfad.web import STATIC_DIR


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SelfAD", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_session_secret(),
        session_cookie="selfad_session",
        max_age=60 * 60 * 24 * 7,
        same_site="lax",
        https_only=use_secure_cookies(),
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(home.router)
    app.include_router(setup.router)
    app.include_router(authentication.router)
    app.include_router(admin.router)
    app.include_router(health.router)

    return app
