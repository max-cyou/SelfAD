import base64
import binascii
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from selfad.gitea_api.client import (
    _request,
)
from selfad.gitea_api.types import (
    GiteaConflict,
    GiteaFileChange,
    GiteaFileContent,
    GiteaFileTooLarge,
    GiteaRepositoryFile,
    GiteaRepositoryNotFound,
    GiteaUnavailable,
    _GiteaRequestError,
)
from selfad.settings import GiteaSettings


def get_repository_file(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
    max_bytes: int = 262_144,
) -> bytes | None:
    file = get_repository_file_content(
        settings,
        repository_path,
        file_path,
        ref=ref,
        max_bytes=max_bytes,
    )
    return file.content if file else None


def get_repository_file_content(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
    max_bytes: int = 262_144,
) -> GiteaFileContent | None:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError:
        return None

    encoded_file_path = "/".join(quote(part, safe="") for part in file_path.split("/"))
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
    sha = payload.get("sha")
    content = payload.get("content")
    encoding = payload.get("encoding")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not isinstance(sha, str)
        or not sha
        or not isinstance(content, str)
        or encoding != "base64"
    ):
        raise GiteaUnavailable("Gitea returned an invalid file response.")
    if size > max_bytes:
        raise GiteaFileTooLarge(f"{file_path} exceeds the {max_bytes} byte limit.")

    try:
        decoded = base64.b64decode("".join(content.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise GiteaUnavailable("Gitea returned invalid file content.") from error
    if len(decoded) > max_bytes:
        raise GiteaFileTooLarge(f"{file_path} exceeds the {max_bytes} byte limit.")
    return GiteaFileContent(path=file_path, sha=sha, content=decoded)


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
    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/archive/{archive_name}"
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
                raise GiteaFileTooLarge(f"Repository archive exceeds the {max_bytes} byte limit.")
            archive = response.read(max_bytes + 1)
    except HTTPError as error:
        if error.code == 404:
            raise GiteaRepositoryNotFound("Gitea repository archive was not found.") from error
        if error.code in {401, 403}:
            raise GiteaUnavailable("Gitea rejected the configured token.") from error
        raise GiteaUnavailable("Gitea could not create the repository archive.") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise GiteaUnavailable("Gitea repository archive is unavailable.") from error

    if len(archive) > max_bytes:
        raise GiteaFileTooLarge(f"Repository archive exceeds the {max_bytes} byte limit.")
    return archive


def repository_file_exists(
    settings: GiteaSettings,
    repository_path: str,
    file_path: str,
    *,
    ref: str,
) -> bool:
    return (
        get_repository_file(
            settings,
            repository_path,
            file_path,
            ref=ref,
            max_bytes=1_048_576,
        )
        is not None
    )


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
    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/{encoded_file_path}"
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


def replace_repository_file(
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
    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/{encoded_file_path}"
    try:
        existing = _request(
            settings,
            "GET",
            f"{path}?ref={quote(branch, safe='')}",
        )
    except _GiteaRequestError as error:
        if error.status_code != 404:
            raise GiteaUnavailable("Gitea could not inspect the repository file.") from error
        existing = None

    payload: dict[str, object] = {
        "branch": branch,
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
    }
    method = "POST"
    if isinstance(existing, dict):
        sha = existing.get("sha")
        if not isinstance(sha, str) or not sha:
            raise GiteaUnavailable("Gitea returned invalid repository file metadata.")
        payload["sha"] = sha
        method = "PUT"

    try:
        _request(settings, method, path, payload)
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Gitea rejected the repository file update.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot update the repository file.") from error
        raise GiteaUnavailable("Gitea could not update the repository file.") from error


def list_repository_files(
    settings: GiteaSettings,
    repository_path: str,
    *,
    ref: str,
) -> list[str]:
    return [
        file.path
        for file in list_repository_file_entries(
            settings,
            repository_path,
            ref=ref,
        )
    ]


def list_repository_file_entries(
    settings: GiteaSettings,
    repository_path: str,
    *,
    ref: str,
) -> list[GiteaRepositoryFile]:
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
    files: list[GiteaRepositoryFile] = []
    for item in tree:
        path = item.get("path") if isinstance(item, dict) else None
        item_type = item.get("type") if isinstance(item, dict) else None
        sha = item.get("sha") if isinstance(item, dict) else None
        size = item.get("size") if isinstance(item, dict) else None
        if (
            isinstance(path, str)
            and item_type == "blob"
            and isinstance(sha, str)
            and sha
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size >= 0
            and path
            and not path.startswith("/")
            and ".." not in path.split("/")
        ):
            files.append(GiteaRepositoryFile(path=path, sha=sha, size=size))
    if len(files) > 2_000:
        raise GiteaFileTooLarge("Repository contains too many files to provision.")
    return files


def change_repository_files(
    settings: GiteaSettings,
    repository_path: str,
    *,
    branch: str,
    message: str,
    changes: list[GiteaFileChange],
    author_name: str,
    author_email: str,
) -> str:
    try:
        owner, name = repository_path.split("/", 1)
    except ValueError as error:
        raise GiteaRepositoryNotFound("Gitea repository path is invalid.") from error
    if not changes:
        raise ValueError("At least one repository file change is required.")

    files: list[dict[str, object]] = []
    for change in changes:
        if change.operation not in {"create", "update", "delete"}:
            raise ValueError("Unsupported repository file operation.")
        payload: dict[str, object] = {
            "operation": change.operation,
            "path": change.path,
        }
        if change.content is not None:
            payload["content"] = base64.b64encode(change.content).decode("ascii")
        if change.sha is not None:
            payload["sha"] = change.sha
        files.append(payload)

    path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents"
    try:
        response = _request(
            settings,
            "POST",
            path,
            {
                "branch": branch,
                "message": message,
                "author": {"name": author_name, "email": author_email},
                "committer": {"name": author_name, "email": author_email},
                "files": files,
            },
        )
    except _GiteaRequestError as error:
        if error.status_code in {409, 422}:
            raise GiteaConflict("Repository changed while the files were submitted.") from error
        if error.status_code in {401, 403}:
            raise GiteaUnavailable("Gitea cannot update the repository.") from error
        if error.status_code == 404:
            raise GiteaRepositoryNotFound("Gitea repository was not found.") from error
        raise GiteaUnavailable("Gitea could not update the repository.") from error

    commit = response.get("commit") if isinstance(response, dict) else None
    commit_sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(commit_sha, str) or not commit_sha:
        raise GiteaUnavailable("Gitea returned an invalid multi-file commit response.")
    return commit_sha
