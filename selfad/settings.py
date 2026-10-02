import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from selfad.database import DATA_DIR

SESSION_SECRET_PATH = DATA_DIR / "session.secret"
GITEA_WEBHOOK_SECRET_PATH = DATA_DIR / "gitea_webhook.secret"
RUNNER_INSTANCE_ID_PATH = DATA_DIR / "runner.instance"
RUNNER_INSTANCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{7,63}$")


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
        return self.internal_runner_enabled or self.docker_host.startswith(
            "unix:///run/selfad-docker/"
        )


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


def public_event_mode_enabled() -> bool:
    value = os.getenv("SELFAD_PUBLIC_EVENT_MODE", "false")
    return value.lower() in {"1", "true", "yes", "on"}


def validate_public_event_settings(database_url: str) -> None:
    """Fail closed when a public deployment uses development-grade settings."""
    if not public_event_mode_enabled():
        return

    errors: list[str] = []
    runner = get_runner_settings()
    gitea = get_gitea_settings()
    if not database_url.startswith("postgresql"):
        errors.append("SELFAD_DATABASE_URL must use PostgreSQL")
    elif "replace-with" in database_url:
        errors.append("SELFAD_DATABASE_URL still contains a placeholder password")
    if not use_secure_cookies():
        errors.append("SELFAD_SECURE_COOKIES must be enabled")
    try:
        trusted_proxies = get_trusted_proxy_hosts()
    except RuntimeError as error:
        errors.append(str(error))
    else:
        if not trusted_proxies:
            errors.append("trusted proxy headers must be configured")
    if not gitea.public_url.startswith("https://"):
        errors.append("SELFAD_GITEA_PUBLIC_URL must use HTTPS")
    if (
        runner.uses_internal_runner
        or runner.internal_runner_enabled
        or not runner.docker_host.startswith("tcp://")
        or not runner.tls_verify
        or not runner.cert_path
    ):
        errors.append("the runner must be an external mTLS Docker endpoint")
    elif runner.cert_path:
        missing_certificates = [
            name
            for name in ("ca.pem", "cert.pem", "key.pem")
            if not (Path(runner.cert_path) / name).is_file()
        ]
        if missing_certificates:
            errors.append(
                "runner TLS directory is missing "
                + ", ".join(missing_certificates)
            )
    if os.getenv("SELFAD_RUNNER_ISOLATION", "") != "dedicated-host":
        errors.append(
            "SELFAD_RUNNER_ISOLATION must acknowledge a dedicated-host runner"
        )
    session_secret = os.getenv("SELFAD_SECRET_KEY", "")
    if len(session_secret) < 32 or session_secret.startswith("replace-"):
        errors.append("SELFAD_SECRET_KEY must contain at least 32 characters")
    metrics_token = os.getenv("SELFAD_METRICS_TOKEN", "")
    if len(metrics_token) < 32 or metrics_token.startswith("replace-"):
        errors.append("SELFAD_METRICS_TOKEN must contain at least 32 characters")
    setup_token = os.getenv("SELFAD_SETUP_TOKEN", "")
    if len(setup_token) < 32 or setup_token.startswith("replace-"):
        errors.append("SELFAD_SETUP_TOKEN must contain at least 32 characters")
    if errors:
        raise RuntimeError(
            "Unsafe public-event configuration: " + "; ".join(errors)
        )


def get_trusted_proxy_hosts() -> list[str] | None:
    """Return explicit trusted proxy addresses, or disable forwarded headers.

    Trusting ``X-Forwarded-For`` from every client lets an attacker evade
    per-IP limits. Production deployments must opt in and name their proxy.
    """
    enabled = os.getenv("SELFAD_TRUST_PROXY_HEADERS", "false").lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    raw_hosts = os.getenv("SELFAD_TRUSTED_PROXY_HOSTS", "").strip()
    hosts = [host.strip() for host in raw_hosts.split(",") if host.strip()]
    if not hosts:
        raise RuntimeError(
            "SELFAD_TRUSTED_PROXY_HOSTS is required when proxy headers are enabled"
        )
    return hosts


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
    password_file = _gitea_root_password_path()
    try:
        return password_file.read_text(encoding="utf-8").strip() or None
    except (FileNotFoundError, PermissionError):
        return None


def set_gitea_root_password(password: str) -> None:
    password_file = _gitea_root_password_path()
    temporary_file = password_file.with_name(f"{password_file.name}.new")
    descriptor = os.open(
        temporary_file,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(password)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary_file, password_file)
    os.chmod(password_file, 0o640)


def _gitea_root_password_path() -> Path:
    return Path(os.getenv(
        "SELFAD_GITEA_PASSWORD_FILE",
        "/data/selfad/secrets/gitea_admin_password",
    ))


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


def get_runner_instance_id() -> str:
    configured = os.getenv("SELFAD_RUNNER_INSTANCE_ID", "").strip()
    if configured:
        if not RUNNER_INSTANCE_ID_PATTERN.fullmatch(configured):
            raise RuntimeError(
                "SELFAD_RUNNER_INSTANCE_ID must contain 8-64 safe characters"
            )
        return configured
    try:
        stored = RUNNER_INSTANCE_ID_PATH.read_text(encoding="utf-8").strip()
        if not RUNNER_INSTANCE_ID_PATTERN.fullmatch(stored):
            raise RuntimeError("Stored runner instance ID is invalid")
        return stored
    except FileNotFoundError:
        pass

    instance_id = secrets.token_hex(16)
    try:
        descriptor = os.open(
            RUNNER_INSTANCE_ID_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return get_runner_instance_id()
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(instance_id)
    return instance_id


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
