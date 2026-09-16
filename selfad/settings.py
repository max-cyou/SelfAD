import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from selfad.database import DATA_DIR


SESSION_SECRET_PATH = DATA_DIR / "session.secret"
GITEA_WEBHOOK_SECRET_PATH = DATA_DIR / "gitea_webhook.secret"


@dataclass(frozen=True)
class GiteaSettings:
    internal_url: str
    public_url: str
    webhook_url: str
    private_token: str | None
    verify_tls: bool

    @property
    def configured(self) -> bool:
        return bool(self.private_token)


@dataclass(frozen=True)
class RunnerSettings:
    docker_host: str
    tls_verify: bool
    cert_path: str | None
    internal_runner_enabled: bool

    @property
    def uses_internal_runner(self) -> bool:
        return self.docker_host.startswith("unix:///run/selfad-docker/")


def get_session_secret() -> str:
    configured_secret = os.getenv("SELFAD_SECRET_KEY")
    if configured_secret:
        if len(configured_secret) < 32:
            raise RuntimeError("SELFAD_SECRET_KEY must contain at least 32 characters")
        return configured_secret

    try:
        return _read_session_secret()
    except FileNotFoundError:
        pass

    secret = secrets.token_urlsafe(48)

    try:
        descriptor = os.open(
            SESSION_SECRET_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return _read_session_secret()

    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(secret)

    return secret


def get_gitea_webhook_secret() -> str:
    configured_secret = os.getenv("SELFAD_GITEA_WEBHOOK_SECRET")
    if configured_secret:
        if len(configured_secret) < 32:
            raise RuntimeError(
                "SELFAD_GITEA_WEBHOOK_SECRET must contain at least 32 characters"
            )
        return configured_secret

    try:
        return _read_gitea_webhook_secret()
    except FileNotFoundError:
        pass

    secret = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(
            GITEA_WEBHOOK_SECRET_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return _read_gitea_webhook_secret()

    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(secret)
    return secret


def use_secure_cookies() -> bool:
    value = os.getenv("SELFAD_SECURE_COOKIES", "false")
    return value.lower() in {"1", "true", "yes", "on"}


def get_gitea_settings() -> GiteaSettings:
    internal_url = os.getenv(
        "SELFAD_GITEA_INTERNAL_URL",
        "http://127.0.0.1:8929",
    )
    public_url = os.getenv("SELFAD_GITEA_PUBLIC_URL", internal_url)
    webhook_url = os.getenv(
        "SELFAD_GITEA_WEBHOOK_URL",
        "http://127.0.0.1:8000/hooks/gitea",
    )
    private_token = os.getenv("SELFAD_GITEA_TOKEN", "").strip() or None
    token_file = os.getenv("SELFAD_GITEA_TOKEN_FILE", "").strip()
    if private_token is None and token_file:
        try:
            private_token = (
                Path(token_file).read_text(encoding="utf-8").strip() or None
            )
        except FileNotFoundError:
            pass
    verify_tls = os.getenv("SELFAD_GITEA_VERIFY_TLS", "true").lower()

    return GiteaSettings(
        internal_url=internal_url.rstrip("/"),
        public_url=public_url.rstrip("/"),
        webhook_url=webhook_url,
        private_token=private_token,
        verify_tls=verify_tls not in {"0", "false", "no", "off"},
    )


def get_gitea_root_password() -> str | None:
    password_file = os.getenv(
        "SELFAD_GITEA_PASSWORD_FILE",
        "/data/selfad/secrets/gitea_admin_password",
    )
    try:
        return Path(password_file).read_text(encoding="utf-8").strip() or None
    except (FileNotFoundError, PermissionError):
        return None


def get_runner_settings() -> RunnerSettings:
    docker_host = os.getenv(
        "SELFAD_RUNNER_DOCKER_HOST",
        "unix:///run/selfad-docker/docker.sock",
    ).strip()
    if not docker_host:
        raise RuntimeError("SELFAD_RUNNER_DOCKER_HOST cannot be empty")
    tls_verify = os.getenv("SELFAD_RUNNER_TLS_VERIFY", "false").lower()
    cert_path = os.getenv("SELFAD_RUNNER_CERT_PATH", "").strip() or None
    internal_runner = os.getenv("SELFAD_ENABLE_INTERNAL_RUNNER", "false").lower()
    return RunnerSettings(
        docker_host=docker_host,
        tls_verify=tls_verify in {"1", "true", "yes", "on"},
        cert_path=cert_path,
        internal_runner_enabled=internal_runner in {"1", "true", "yes", "on"},
    )


def get_metrics_token() -> str | None:
    return os.getenv("SELFAD_METRICS_TOKEN", "").strip() or None


def get_rate_limit(name: str, *, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        return min(10_000, max(1, int(raw_value)))
    except ValueError:
        return default


def get_worker_concurrency() -> int:
    raw_value = os.getenv("SELFAD_WORKER_CONCURRENCY", "1").strip()
    try:
        return min(16, max(1, int(raw_value)))
    except ValueError:
        return 1


def _read_session_secret() -> str:
    secret = SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("Stored session secret is invalid")
    return secret


def _read_gitea_webhook_secret() -> str:
    secret = GITEA_WEBHOOK_SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("Stored Gitea webhook secret is invalid")
    return secret
