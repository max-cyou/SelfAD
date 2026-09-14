import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from selfad.database import SessionLocal, initialize_database
from selfad.gitea import (
    GiteaError,
    ensure_repository_webhook,
    get_authenticated_user,
)
from selfad.models import Service, User
from selfad.routes import (
    admin,
    authentication,
    health,
    home,
    participants,
    setup,
    webhooks,
)
from selfad.settings import (
    get_gitea_settings,
    get_gitea_webhook_secret,
    get_session_secret,
    use_secure_cookies,
)
from selfad.web import STATIC_DIR
from selfad.worker import run_repository_worker


logger = logging.getLogger(__name__)


def reconcile_administrator_gitea_identity() -> None:
    settings = get_gitea_settings()
    if not settings.configured:
        return

    with SessionLocal() as session:
        administrator = session.scalar(
            select(User)
            .where(User.is_admin.is_(True), User.gitea_username.is_(None))
            .order_by(User.id)
        )
        if administrator is None:
            return
        try:
            gitea_user = get_authenticated_user(settings)
        except GiteaError as error:
            logger.warning("Could not map the administrator to Gitea: %s", error)
            return
        administrator.gitea_user_id = gitea_user.id
        administrator.gitea_username = gitea_user.username
        session.commit()


def reconcile_repository_webhooks() -> None:
    settings = get_gitea_settings()
    if not settings.configured:
        return

    secret = get_gitea_webhook_secret()
    with SessionLocal() as session:
        services = session.scalars(select(Service)).all()
        for service in services:
            for repository_path in (
                service.repository_path,
                service.jury_repository_path,
            ):
                if not repository_path:
                    continue
                try:
                    ensure_repository_webhook(
                        settings,
                        repository_path,
                        secret=secret,
                        branch_filter=service.default_branch,
                    )
                except GiteaError as error:
                    logger.warning(
                        "Could not reconcile webhook for %s: %s",
                        repository_path,
                        error,
                    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    reconcile_administrator_gitea_identity()
    reconcile_repository_webhooks()
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_repository_worker(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


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
    app.include_router(participants.router)
    app.include_router(admin.router)
    app.include_router(webhooks.router)
    app.include_router(health.router)

    return app
