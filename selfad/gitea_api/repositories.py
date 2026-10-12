from urllib.parse import quote

from selfad.gitea_api.client import (
    _parse_repository,
    _request,
)
from selfad.gitea_api.types import (
    GITEA_REPOSITORY_OWNER,
    GiteaConflict,
    GiteaRepository,
    GiteaRepositoryNotFound,
    GiteaUnavailable,
    _GiteaRequestError,
)
from selfad.settings import GiteaSettings


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
        response = _request(
            settings,
            "POST",
            f"/admin/users/{GITEA_REPOSITORY_OWNER}/repos",
            payload,
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("A Gitea repository with this path already exists.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot create repositories with this token.") from error
        raise GiteaUnavailable("Gitea could not create the repository.") from error
    return _parse_repository(response)


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


def remove_repository_collaborator(
    settings: GiteaSettings,
    repository_path: str,
    *,
    username: str,
) -> None:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error
    path = (
        f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        f"/collaborators/{quote(username, safe='')}"
    )
    try:
        _request(settings, "DELETE", path)
    except _GiteaRequestError as error:
        if error.status_code == 404:
            return
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot revoke repository access.") from error
        raise GiteaUnavailable("Gitea could not revoke repository access.") from error


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

    repository_api_path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
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
