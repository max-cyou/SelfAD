import os
import secrets

from selfad.database import DATA_DIR


SESSION_SECRET_PATH = DATA_DIR / "session.secret"


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


def use_secure_cookies() -> bool:
    value = os.getenv("SELFAD_SECURE_COOKIES", "false")
    return value.lower() in {"1", "true", "yes", "on"}


def _read_session_secret() -> str:
    secret = SESSION_SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret) < 32:
        raise RuntimeError("Stored session secret is invalid")
    return secret
