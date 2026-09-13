from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from selfad.database import initialize_database
from selfad.routes import health, home, setup
from selfad.web import STATIC_DIR


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SelfAD", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    app.include_router(home.router)
    app.include_router(setup.router)
    app.include_router(health.router)

    return app
