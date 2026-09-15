import base64
import binascii
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


class GiteaFileTooLarge(GiteaError):
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
class GiteaUser:
    id: int
    username: str


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


def get_branch_commit(
    settings: GiteaSettings,
    repository_path: str,
    *,
    branch: str | None = None,
) -> str:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error

    repository = get_repository(settings, repository_path)
    branch_name = branch or repository.default_branch
    if not branch_name:
        raise GiteaRepositoryNotFound("Gitea repository has no default branch.")

    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/branches/{quote(branch_name, safe='')}"
    )
    try:
        payload = _request(settings, "GET", path)
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea branch was not found.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not inspect the branch.") from error

    commit = payload.get("commit") if isinstance(payload, dict) else None
    commit_id = commit.get("id") if isinstance(commit, dict) else None
    if not isinstance(commit_id, str) or not commit_id:
        raise GiteaUnavailable("Gitea returned an invalid branch response.")
    return commit_id


def get_repository_file(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
    max_bytes: int = 262_144,
) -> bytes | None:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError:
        return None

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
            return None
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not inspect the repository.") from error

    if not isinstance(payload, dict) or payload.get("type") != "file":
        return None

    size = payload.get("size")
    content = payload.get("content")
    encoding = payload.get("encoding")
    if not isinstance(size, int) or not isinstance(content, str) or encoding != "base64":
        raise GiteaUnavailable("Gitea returned an invalid file response.")
    if size > max_bytes:
        raise GiteaFileTooLarge(f"{file_path} exceeds the {max_bytes} byte limit.")

    try:
        decoded = base64.b64decode("".join(content.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise GiteaUnavailable("Gitea returned invalid file content.") from error
    if len(decoded) > max_bytes:
        raise GiteaFileTooLarge(f"{file_path} exceeds the {max_bytes} byte limit.")
    return decoded


def download_repository_archive(
    settings: GiteaSettings,
    repository_path: str,
    *,
    ref: str,
    max_bytes: int = 32 * 1024 * 1024,
) -> bytes:
    if not settings.private_token:
        raise GiteaUnavailable("Gitea API token is not configured.")
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error

    archive_name = quote(f"{ref}.tar.gz", safe=".")
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/archive/{archive_name}"
    )
    request = Request(
        f"{settings.internal_url}/api/v1{path}",
        method="GET",
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"token {settings.private_token}",
        },
    )
    context = None
    if not settings.verify_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(request, timeout=30, context=context) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise GiteaFileTooLarge(
                    f"Repository archive exceeds the {max_bytes} byte limit."
                )
            archive = response.read(max_bytes + 1)
    except HTTPError as error:
        if error.code == 404:
            raise GiteaRepositoryNotFound(
                "Gitea repository archive was not found."
            ) from error
        if error.code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not create the repository archive.") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise GiteaUnavailable("Gitea repository archive is unavailable.") from error

    if len(archive) > max_bytes:
        raise GiteaFileTooLarge(
            f"Repository archive exceeds the {max_bytes} byte limit."
        )
    return archive


def repository_file_exists(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
) -> bool:
    return get_repository_file(
        settings,
        repository_path,
        file_path,
        ref=ref,
        max_bytes=1_048_576,
    ) is not None


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
    credentials = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
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


def add_repository_collaborator(
    settings: GiteaSettings,
    repository_path: str,
    *,
    username: str,
    permission: str,
) -> None:
    if permission not in {"read", "write"}:
        raise ValueError("Unsupported Gitea collaborator permission.")
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/collaborators/{quote(username, safe='')}"
    )
    try:
        _request(settings, "PUT", path, {"permission": permission})
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository or user was not found.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot grant repository access.") from error
        raise GiteaUnavailable("Gitea could not grant repository access.") from error


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


def create_repository_file(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    content: bytes,
    branch: str,
    message: str,
) -> None:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error
    encoded_file_path = "/".join(quote(part, safe="") for part in file_path.split("/"))
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/contents/{encoded_file_path}"
    )
    try:
        _request(
            settings,
            "POST",
            path,
            {
                "branch": branch,
                "message": message,
                "content": base64.b64encode(content).decode("ascii"),
            },
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Gitea rejected a repository seed file.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot seed the repository.") from error
        raise GiteaUnavailable("Gitea could not seed the repository.") from error


def list_repository_files(
    settings: GiteaSettings,
    repository_path: str,
    *,
    ref: str,
) -> list[str]:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/git/trees/{quote(ref, safe='')}?recursive=true"
    )
    try:
        response = _request(settings, "GET", path)
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository tree was not found.") from error
        raise GiteaUnavailable("Gitea could not read the repository tree.") from error
    tree = response.get("tree") if isinstance(response, dict) else None
    if not isinstance(tree, list):
        raise GiteaUnavailable("Gitea returned an invalid repository tree.")
    files: list[str] = []
    for item in tree:
        path = item.get("path") if isinstance(item, dict) else None
        item_type = item.get("type") if isinstance(item, dict) else None
        if (
            isinstance(path, str)
            and item_type == "blob"
            and path
            and not path.startswith("/")
            and ".." not in path.split("/")
        ):
            files.append(path)
    if len(files) > 2_000:
        raise GiteaFileTooLarge("Repository contains too many files to provision.")
    return files


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


def ensure_repository_webhook(
    settings: GiteaSettings,
    repository_path: str,
    *,
    secret: str,
    branch_filter: str,
) -> int:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error

    repository_api_path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
    )
    try:
        hooks = _request(settings, "GET", f"{repository_api_path}/hooks?limit=50")
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository was not found.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not inspect repository webhooks.") from error

    if isinstance(hooks, list):
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            config = hook.get("config")
            events = hook.get("events")
            hook_id = hook.get("id")
            if (
                isinstance(config, dict)
                and config.get("url") == settings.webhook_url
                and isinstance(events, list)
                and "push" in events
                and hook.get("branch_filter") == branch_filter
                and hook.get("active") is True
                and isinstance(hook_id, int)
            ):
                return hook_id

    payload: dict[str, object] = {
        "type": "gitea",
        "active": True,
        "events": ["push"],
        "branch_filter": branch_filter,
        "config": {
            "url": settings.webhook_url,
            "content_type": "json",
            "secret": secret,
        },
    }
    try:
        response = _request(
            settings,
            "POST",
            f"{repository_api_path}/hooks",
            payload,
        )
    except _GiteaRequestError as error:
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository was not found.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot configure repository webhooks.") from error
        raise GiteaUnavailable("Gitea could not create the repository webhook.") from error

    hook_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(hook_id, int):
        raise GiteaUnavailable("Gitea returned an invalid webhook response.")
    return hook_id


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
    webhook_secret: str,
) -> ProvisionedService:
    ssh_key_id = ensure_ssh_key(settings, ssh_public_key)
    service_repository = create_repository(
        settings,
        path=slug,
        description=description,
        default_branch=default_branch,
    )
    jury_repository: GiteaRepository | None = None
    try:
        jury_repository = create_repository(
            settings,
            path=f"{slug}-jury",
            description=f"Private SelfAD jury files for {name}.",
            default_branch=default_branch,
        )
        ensure_repository_webhook(
            settings,
            service_repository.path,
            secret=webhook_secret,
            branch_filter=default_branch,
        )
        ensure_repository_webhook(
            settings,
            jury_repository.path,
            secret=webhook_secret,
            branch_filter=default_branch,
        )
    except GiteaError:
        if jury_repository is not None:
            try:
                delete_repository(settings, jury_repository.path)
            except GiteaError:
                pass
        try:
            delete_repository(settings, service_repository.path)
        except GiteaError:
            pass
        raise
    assert jury_repository is not None
    return ProvisionedService(service_repository, jury_repository, ssh_key_id)
