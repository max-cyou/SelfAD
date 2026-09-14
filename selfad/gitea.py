import json
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from selfad.settings import GiteaSettings


class GiteaError(Exception):
    pass


class GiteaRepositoryNotFound(GiteaError):
    pass


class GiteaUnavailable(GiteaError):
    pass


class GiteaConflict(GiteaError):
    pass


class _GiteaRequestError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


@dataclass(frozen=True)
class GiteaRepository:
    id: int
    path: str
    default_branch: str | None
    empty: bool


@dataclass(frozen=True)
class ProvisionedService:
    service_repository: GiteaRepository
    jury_repository: GiteaRepository
    ssh_key_id: int


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


def get_repository(
    settings: GiteaSettings,
    repository_path: str,
) -> GiteaRepository:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error

    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    try:
        payload = _request(settings, "GET", path)
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository was not found.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea API returned an error.") from error
    return _parse_repository(payload)


def repository_file_exists(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
) -> bool:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError:
        return False

    encoded_file_path = "/".join(
        quote(part, safe="") for part in file_path.split("/")
    )
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/contents/{encoded_file_path}?ref={quote(ref, safe='')}"
    )
    try:
        payload = _request(settings, "GET", path)
    except _GiteaRequestError as error:
        if error.status_code == 404:
            return False
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not inspect the repository.") from error

    return isinstance(payload, dict) and payload.get("type") == "file"


def create_repository(
    settings: GiteaSettings,
    *,
    path: str,
    description: str,
    default_branch: str,
) -> GiteaRepository:
    payload: dict[str, object] = {
        "name": path,
        "description": description,
        "private": True,
        "auto_init": False,
        "default_branch": default_branch,
    }
    try:
        response = _request(settings, "POST", "/user/repos", payload)
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("A Gitea repository with this path already exists.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot create repositories with this token.") from error
        raise GiteaUnavailable("Gitea could not create the repository.") from error
    return _parse_repository(response)


def delete_repository(settings: GiteaSettings, repository_path: str) -> None:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError:
        return

    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    try:
        _request(settings, "DELETE", path)
    except _GiteaRequestError as error:
        if error.status_code != 404:
            raise GiteaUnavailable("Gitea could not remove the repository.") from error


def ensure_ssh_key(settings: GiteaSettings, public_key: str) -> int:
    try:
        keys = _request(settings, "GET", "/user/keys?limit=100")
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
            "/user/keys",
            {"title": "SelfAD administrator", "key": public_key},
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


def provision_service(
    settings: GiteaSettings,
    *,
    name: str,
    slug: str,
    description: str,
    default_branch: str,
    ssh_public_key: str,
) -> ProvisionedService:
    ssh_key_id = ensure_ssh_key(settings, ssh_public_key)
    service_repository = create_repository(
        settings,
        path=slug,
        description=description,
        default_branch=default_branch,
    )
    try:
        jury_repository = create_repository(
            settings,
            path=f"{slug}-jury",
            description=f"Private SelfAD jury files for {name}.",
            default_branch=default_branch,
        )
    except GiteaError:
        try:
            delete_repository(settings, service_repository.path)
        except GiteaError:
            pass
        raise
    return ProvisionedService(service_repository, jury_repository, ssh_key_id)
