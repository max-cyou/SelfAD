from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from selfad.auth import csrf_token_is_valid
from selfad.database import get_session
from selfad.editor import (
    MAX_EDITOR_FILE_BYTES,
    EditorSubmittedChange,
    EditorValidationError,
    EditorWorkspace,
    normalize_editor_path,
    path_is_editable,
    prepare_editor_changes,
)
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaFileTooLarge,
    change_repository_files,
    get_branch_commit,
    get_repository_file_content,
    list_repository_file_entries,
)
from selfad.models import ParticipantService, Service, ServiceStatus, User
from selfad.rate_limit import rate_limiter
from selfad.routes.participants import participant_access
from selfad.scoring import get_scoring_settings
from selfad.settings import get_gitea_settings

router = APIRouter(prefix="/api/editor", tags=["editor"])
EditorKind = Literal["source", "attack", "defense"]


class EditorChangeInput(BaseModel):
    operation: Literal["create", "update", "delete"]
    path: str
    content: str | None = None
    sha: str | None = None


class EditorSubmissionInput(BaseModel):
    base_commit: str
    changes: list[EditorChangeInput]


def _authenticated_user(request: Request, session: Session) -> User:
    user, redirect = participant_access(request, session)
    if redirect:
        location = redirect.headers.get("location")
        raise HTTPException(status_code=401 if location == "/login" else 403)
    if user is None:
        raise HTTPException(status_code=401)
    return user


def _participant_service(
    session: Session,
    *,
    user_id: int,
    assignment_id: int,
) -> tuple[ParticipantService, Service]:
    row = session.execute(
        select(ParticipantService, Service)
        .join(Service, Service.id == ParticipantService.service_id)
        .where(
            ParticipantService.id == assignment_id,
            ParticipantService.user_id == user_id,
            Service.status == ServiceStatus.ACTIVE,
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Service assignment not found.")
    return row


def _workspace(
    session: Session,
    *,
    assignment: ParticipantService,
    service: Service,
    kind: EditorKind,
) -> EditorWorkspace:
    if kind == "source":
        if not service.runtime_source_commit:
            raise HTTPException(status_code=409, detail="Service source is unavailable.")
        return EditorWorkspace(
            kind=kind,
            repository_path=service.repository_path,
            ref=service.runtime_source_commit,
            branch=None,
            editable=False,
        )
    if kind == "defense" and not assignment.defense_unlocked:
        raise HTTPException(status_code=403, detail="Defense is still locked.")

    repository_path = (
        assignment.attack_repository_path
        if kind == "attack"
        else assignment.defense_repository_path
    )
    try:
        head = get_branch_commit(
            get_gitea_settings(),
            repository_path,
            branch=service.default_branch,
        )
    except GiteaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    scoring = get_scoring_settings(session)
    return EditorWorkspace(
        kind=kind,
        repository_path=repository_path,
        ref=head,
        branch=service.default_branch,
        editable=True,
        allow_attack_requirements=(
            kind == "attack" and scoring.allow_user_attack_requirements
        ),
    )


def _context(
    request: Request,
    session: Session,
    *,
    assignment_id: int,
    kind: EditorKind,
) -> tuple[User, ParticipantService, Service, EditorWorkspace]:
    user = _authenticated_user(request, session)
    assignment, service = _participant_service(
        session,
        user_id=user.id,
        assignment_id=assignment_id,
    )
    workspace = _workspace(
        session,
        assignment=assignment,
        service=service,
        kind=kind,
    )
    return user, assignment, service, workspace


@router.get("/{assignment_id}/{kind}/tree")
def editor_tree(
    assignment_id: int,
    kind: EditorKind,
    request: Request,
    session: Session = Depends(get_session),
):
    _, _, service, workspace = _context(
        request,
        session,
        assignment_id=assignment_id,
        kind=kind,
    )
    try:
        files = list_repository_file_entries(
            get_gitea_settings(), workspace.repository_path,
            ref=workspace.ref,
        )
    except GiteaFileTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except GiteaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "assignment_id": assignment_id,
        "service": service.name,
        "kind": workspace.kind,
        "head": workspace.ref,
        "editable": workspace.editable,
        "files": [
            {
                "path": file.path,
                "sha": file.sha,
                "size": file.size,
                "editable": path_is_editable(workspace, file.path),
            }
            for file in files
        ],
    }


@router.get("/{assignment_id}/{kind}/file")
def editor_file(
    assignment_id: int,
    kind: EditorKind,
    request: Request,
    path: str = Query(min_length=1, max_length=512),
    session: Session = Depends(get_session),
):
    _, _, _, workspace = _context(
        request,
        session,
        assignment_id=assignment_id,
        kind=kind,
    )
    try:
        normalized_path = normalize_editor_path(path)
    except EditorValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        file = get_repository_file_content(
            get_gitea_settings(),
            workspace.repository_path,
            normalized_path,
            ref=workspace.ref,
            max_bytes=MAX_EDITOR_FILE_BYTES,
        )
    except GiteaFileTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except GiteaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if file is None:
        raise HTTPException(status_code=404, detail="Repository file not found.")
    try:
        content = file.content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=415, detail="Binary files cannot be edited.") from error
    if "\0" in content:
        raise HTTPException(status_code=415, detail="Binary files cannot be edited.")
    return {
        "path": file.path,
        "sha": file.sha,
        "content": content,
        "editable": path_is_editable(workspace, file.path),
        "head": workspace.ref,
    }


@router.post("/{assignment_id}/{kind}/submit", status_code=201)
def editor_submit(
    assignment_id: int,
    kind: EditorKind,
    submission: EditorSubmissionInput,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
):
    user, _, service, workspace = _context(
        request,
        session,
        assignment_id=assignment_id,
        kind=kind,
    )
    if not csrf_token_is_valid(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")
    if not rate_limiter.allow(
        f"editor-submit:{user.id}",
        limit=12,
        window_seconds=60,
    ):
        raise HTTPException(status_code=429, detail="Too many editor submissions.")
    if workspace.branch is None:
        raise HTTPException(status_code=403, detail="This workspace is read-only.")
    if submission.base_commit != workspace.ref:
        raise HTTPException(
            status_code=409,
            detail="Repository changed after the editor was opened. Reload before submitting.",
        )
    try:
        changes = prepare_editor_changes(
            workspace,
            [
                EditorSubmittedChange(
                    operation=change.operation,
                    path=change.path,
                    content=change.content,
                    sha=change.sha,
                )
                for change in submission.changes
            ],
        )
    except EditorValidationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    try:
        commit_sha = change_repository_files(
            get_gitea_settings(), workspace.repository_path,
            branch=workspace.branch,
            message=f"Submit {kind} solution from SelfAD editor",
            changes=changes,
            author_name=user.username,
            author_email=user.email,
        )
    except GiteaConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except GiteaFileTooLarge as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except GiteaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "commit": commit_sha,
        "kind": kind,
        "service": service.name,
        "message": "Submission committed. The checker will start from the webhook.",
    }
