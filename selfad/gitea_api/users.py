import base64
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from selfad.gitea_api.client import (
    _request,
)
from selfad.gitea_api.types import (
    GITEA_REPOSITORY_OWNER,
    GiteaConflict,
    GiteaUnavailable,
    GiteaUser,
    _GiteaRequestError,
)
from selfad.settings import GiteaSettings


def create_gitea_user(
    settings: GiteaSettings,
    *,
    username: str,
    email: str,
    password: str,
) -> GiteaUser:
    try:
        response = _request(
            settings,
            "POST",
            "/admin/users",
            {
                "username": username,
                "email": email,
                "password": password,
                "must_change_password": False,
                "restricted": False,
                "send_notify": False,
                "visibility": "private",
            },
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("A Gitea user with this name already exists.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot create participants.") from error
        raise GiteaUnavailable("Gitea could not create the participant.") from error
    user_id = response.get("id") if isinstance(response, dict) else None
    returned_username = response.get("login") if isinstance(response, dict) else None
    if not isinstance(user_id, int) or not isinstance(returned_username, str):
        raise GiteaUnavailable("Gitea returned an invalid user response.")
    return GiteaUser(user_id, returned_username)


def gitea_username_exists(settings: GiteaSettings, *, username: str) -> bool:
    try:
        _request(
            settings,
            "GET",
            f"/users/{quote(username, safe='')}",
        )
    except _GiteaRequestError as error:
        if error.status_code == 404:
            return False
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not check the username.") from error
    return True


def get_authenticated_user(settings: GiteaSettings) -> GiteaUser:
    try:
        response = _request(settings, "GET", "/user")
    except _GiteaRequestError as error:
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not inspect its administrator.") from error

    user_id = response.get("id") if isinstance(response, dict) else None
    username = response.get("login") if isinstance(response, dict) else None
    if not isinstance(user_id, int) or not isinstance(username, str):
        raise GiteaUnavailable("Gitea returned an invalid user response.")
    return GiteaUser(user_id, username)


def authenticate_gitea_user(
    settings: GiteaSettings,
    *,
    username: str,
    password: str,
) -> GiteaUser | None:
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{settings.internal_url}/api/v1/user",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {credentials}",
        },
    )
    context = None
    if not settings.verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(request, timeout=8, context=context) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code in {401, 403}:
            return None
        raise GiteaUnavailable("Gitea authentication is unavailable.") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise GiteaUnavailable("Gitea authentication is unavailable.") from error

    user_id = payload.get("id") if isinstance(payload, dict) else None
    returned_username = payload.get("login") if isinstance(payload, dict) else None
    if not isinstance(user_id, int) or not isinstance(returned_username, str):
        raise GiteaUnavailable("Gitea returned an invalid user response.")
    return GiteaUser(user_id, returned_username)


def update_gitea_user(
    settings: GiteaSettings,
    *,
    username: str,
    email: str,
    password: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "source_id": 0,
        "email": email,
        "login_name": username,
    }
    if password:
        payload["password"] = password
        payload["must_change_password"] = False
    try:
        _request(
            settings,
            "PATCH",
            f"/admin/users/{quote(username, safe='')}",
            payload,
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Gitea rejected these user details.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot update this user.") from error
        raise GiteaUnavailable("Gitea could not update this user.") from error


def delete_gitea_user(settings: GiteaSettings, *, username: str) -> None:
    try:
        _request(
            settings,
            "DELETE",
            f"/admin/users/{quote(username, safe='')}?purge=true",
        )
    except _GiteaRequestError as error:
        if error.status_code == 404:
            return
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot delete this user.") from error
        raise GiteaUnavailable("Gitea could not delete this user.") from error


def add_user_ssh_key(
    settings: GiteaSettings,
    *,
    username: str,
    public_key: str,
) -> int:
    try:
        response = _request(
            settings,
            "POST",
            f"/admin/users/{quote(username, safe='')}/keys",
            {"title": "SelfAD participant", "key": public_key, "read_only": False},
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Gitea rejected this SSH public key.") from error
        raise GiteaUnavailable("Gitea could not add the participant SSH key.") from error
    key_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(key_id, int):
        raise GiteaUnavailable("Gitea returned an invalid SSH key response.")
    return key_id


def delete_user_ssh_key(
    settings: GiteaSettings,
    *,
    username: str,
    key_id: int,
) -> None:
    try:
        _request(
            settings,
            "DELETE",
            f"/admin/users/{quote(username, safe='')}/keys/{key_id}",
        )
    except _GiteaRequestError as error:
        if error.status_code == 404:
            return
        raise GiteaUnavailable("Gitea could not remove the previous SSH key.") from error


def ensure_ssh_key(
    settings: GiteaSettings,
    public_key: str,
    *,
    username: str = GITEA_REPOSITORY_OWNER,
) -> int:
    try:
        keys = _request(
            settings,
            "GET",
            f"/users/{quote(username, safe='')}/keys?limit=100",
        )
    except _GiteaRequestError as error:
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not read SSH keys.") from error

    key_identity = " ".join(public_key.split()[:2])
    if isinstance(keys, list):
        for key in keys:
            existing_key = key.get("key") if isinstance(key, dict) else None
            existing_id = key.get("id") if isinstance(key, dict) else None
            if (
                isinstance(existing_key, str)
                and " ".join(existing_key.split()[:2]) == key_identity
                and isinstance(existing_id, int)
            ):
                return existing_id

    try:
        response = _request(
            settings,
            "POST",
            f"/admin/users/{quote(username, safe='')}/keys",
            {
                "title": "SelfAD administrator",
                "key": public_key,
                "read_only": False,
            },
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Gitea rejected this SSH public key.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot add SSH keys with this token.") from error
        raise GiteaUnavailable("Gitea could not add the SSH key.") from error

    key_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(key_id, int):
        raise GiteaUnavailable("Gitea returned an invalid SSH key response.")
    return key_id
