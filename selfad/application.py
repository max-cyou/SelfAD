import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from selfad.database import (
    DATABASE_URL,
    SessionLocal,
    application_instance_lock,
    initialize_database,
)
from selfad.gitea import (
    GiteaError,
    ensure_repository_webhook,
    get_authenticated_user,
)
from selfad.models import ScoringSettings, Service, User
from selfad.routes import (
    admin,
    authentication,
    health,
    home,
    participants,
    setup,
    webhooks,
)
from selfad.runner import cleanup_managed_runner_resources
from selfad.settings import (
    get_gitea_settings,
    get_gitea_webhook_secret,
    get_session_secret,
    get_trusted_proxy_hosts,
    get_worker_concurrency,
    public_event_mode_enabled,
    use_secure_cookies,
    validate_public_event_settings,
)
from selfad.web import STATIC_DIR
from selfad.worker import recover_interrupted_repository_events, run_repository_worker

logger = logging.getLogger(__name__)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"
    ),
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


def validate_public_event_database_policy() -> None:
    if not public_event_mode_enabled():
        return
    with SessionLocal() as session:
        scoring = session.get(ScoringSettings, 1)
        if scoring and scoring.allow_user_attack_requirements:
            raise RuntimeError(
                "Unsafe public-event scoring: participant requirements are enabled."
            )


def apply_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


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
    validate_public_event_settings(DATABASE_URL)
    with application_instance_lock():
        initialize_database()
        validate_public_event_database_policy()
        recover_interrupted_repository_events()
        cleanup_managed_runner_resources()
        reconcile_administrator_gitea_identity()
        reconcile_repository_webhooks()
        stop_event = asyncio.Event()
        worker_tasks = [
            asyncio.create_task(run_repository_worker(stop_event))
            for _ in range(get_worker_concurrency())
        ]
        try:
            yield
        finally:
            stop_event.set()
            for worker_task in worker_tasks:
                worker_task.cancel()
            for worker_task in worker_tasks:
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass


def create_app() -> FastAPI:
    app = FastAPI(title="SelfAD", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(request, call_next):
        return apply_security_headers(await call_next(request))

    trusted_proxy_hosts = get_trusted_proxy_hosts()
    if trusted_proxy_hosts:
        app.add_middleware(
            ProxyHeadersMiddleware,
            trusted_hosts=trusted_proxy_hosts,
        )
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
