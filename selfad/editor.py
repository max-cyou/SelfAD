import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from selfad.gitea import GiteaFileChange

EDITOR_OPERATIONS = {"create", "update", "delete"}
MAX_EDITOR_FILE_BYTES = 256 * 1024
MAX_EDITOR_SUBMISSION_BYTES = 1024 * 1024
MAX_EDITOR_CHANGES = 50
OBJECT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")


class EditorValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EditorWorkspace:
    kind: str
    repository_path: str
    ref: str
    branch: str | None
    editable: bool
    allow_attack_requirements: bool = False


@dataclass(frozen=True)
class EditorSubmittedChange:
    operation: str
    path: str
    content: str | None
    sha: str | None


def normalize_editor_path(path: str) -> str:
    if not path or len(path) > 512 or "\0" in path or "\\" in path:
        raise EditorValidationError("Invalid repository file path.")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or str(parsed) != path
        or any(part in {"", ".", "..", ".git"} for part in parsed.parts)
    ):
        raise EditorValidationError("Invalid repository file path.")
    return path


def path_is_editable(workspace: EditorWorkspace, path: str) -> bool:
    if not workspace.editable or path == "Dockerfile":
        return False
    if (
        workspace.kind == "attack"
        and path == "requirements.txt"
        and not workspace.allow_attack_requirements
    ):
        return False
    return True


def prepare_editor_changes(
    workspace: EditorWorkspace,
    changes: list[EditorSubmittedChange],
) -> list[GiteaFileChange]:
    if not workspace.editable:
        raise EditorValidationError("This workspace is read-only.")
    if not changes:
        raise EditorValidationError("No file changes were submitted.")
    if len(changes) > MAX_EDITOR_CHANGES:
        raise EditorValidationError(
            f"A submission may change at most {MAX_EDITOR_CHANGES} files."
        )

    prepared: list[GiteaFileChange] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for change in changes:
        path = normalize_editor_path(change.path)
        if path in seen_paths:
            raise EditorValidationError("A file may only appear once per submission.")
        seen_paths.add(path)
        if change.operation not in EDITOR_OPERATIONS:
            raise EditorValidationError("Unsupported file operation.")
        if not path_is_editable(workspace, path):
            raise EditorValidationError(f"{path} is read-only.")

        if change.operation in {"update", "delete"}:
            if not change.sha or not OBJECT_SHA_PATTERN.fullmatch(change.sha):
                raise EditorValidationError(
                    f"{path} is missing its current object identifier."
                )
        elif change.sha is not None:
            raise EditorValidationError("New files must not include an object identifier.")

        content: bytes | None = None
        if change.operation in {"create", "update"}:
            if change.content is None or "\0" in change.content:
                raise EditorValidationError(f"{path} must contain UTF-8 text.")
            content = change.content.encode("utf-8")
            if len(content) > MAX_EDITOR_FILE_BYTES:
                raise EditorValidationError(
                    f"{path} exceeds the {MAX_EDITOR_FILE_BYTES} byte limit."
                )
            total_bytes += len(content)
        elif change.content is not None:
            raise EditorValidationError("Deleted files must not include content.")

        prepared.append(
            GiteaFileChange(
                operation=change.operation,
                path=path,
                content=content,
                sha=change.sha,
            )
        )

    if total_bytes > MAX_EDITOR_SUBMISSION_BYTES:
        raise EditorValidationError(
            f"A submission exceeds the {MAX_EDITOR_SUBMISSION_BYTES} byte limit."
        )
    return prepared
