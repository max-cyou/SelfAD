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
