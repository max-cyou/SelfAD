import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from selfad.gitea_api.types import (
    GiteaRepository,
    GiteaUnavailable,
    _GiteaRequestError,
)
from selfad.settings import GiteaSettings


def _request(
    settings: GiteaSettings,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
):
    if not settings.private_token:
        raise GiteaUnavailable("Gitea API token is not configured.")

    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{settings.internal_url}/api/v1{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"token {settings.private_token}",
        },
    )
    context = None
    if not settings.verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(request, timeout=8, context=context) as response:
            body = response.read()
            if not body:
                return None
            return json.loads(body)
    except HTTPError as error:
        raise _GiteaRequestError(error.code) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise GiteaUnavailable("Gitea API is unavailable.") from error


def _parse_repository(payload) -> GiteaRepository:
    repository_id = payload.get("id") if isinstance(payload, dict) else None
    path = payload.get("full_name") if isinstance(payload, dict) else None
    default_branch = payload.get("default_branch") if isinstance(payload, dict) else None
    empty = payload.get("empty") if isinstance(payload, dict) else None
    if (
        not isinstance(repository_id, int)
        or isinstance(repository_id, bool)
        or not isinstance(path, str)
        or not isinstance(empty, bool)
    ):
        raise GiteaUnavailable("Gitea returned an invalid repository response.")
    if default_branch is not None and not isinstance(default_branch, str):
        raise GiteaUnavailable("Gitea returned an invalid repository response.")
    return GiteaRepository(repository_id, path, default_branch, empty)
