import hmac
import secrets

from fastapi import Request
from sqlalchemy.orm import Session

from selfad.models import User


def get_session_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None

    return session.get(User, user_id)


def sign_in(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id


def sign_out(request: Request) -> None:
    request.session.clear()


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def csrf_token_is_valid(request: Request, submitted_token: object) -> bool:
    session_token = request.session.get("csrf_token")
    return (
        isinstance(session_token, str)
        and isinstance(submitted_token, str)
        and hmac.compare_digest(session_token, submitted_token)
    )
