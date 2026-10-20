import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.admission import snapshot_admission
from selfad.contest import STARTED, contest_state
from selfad.database import get_session
from selfad.gitea import GiteaError, get_branch_commit
from selfad.models import (
    InstanceConfig,
    ParticipantRepositoryStatus,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
)
from selfad.settings import get_gitea_settings, get_gitea_webhook_secret

router = APIRouter()

MAX_WEBHOOK_BYTES = 1_048_576
MAX_PENDING_EVENTS_PER_REPOSITORY = 25
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def webhook_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status_code)


async def verify_head(session, repository_path, branch, commit_sha):
    """Check freshness under the repository lock; execution still pins SHA."""
    if session.scalar(select(RepositoryEvent.id).where(
        RepositoryEvent.repository_path == repository_path,
        RepositoryEvent.ref == f"refs/heads/{branch}",
        RepositoryEvent.commit_sha == commit_sha,
    ).limit(1)):
        return webhook_response("Commit already received.", 202)
    try:
        head = await run_in_threadpool(get_branch_commit, get_gitea_settings(),
                                      repository_path, branch=branch)
    except GiteaError:
        return webhook_response("Could not verify push HEAD; retry delivery.", 503)
    if head.lower() != commit_sha:
        return webhook_response("Stale push ignored: branch has moved.", 202)
    return None


@router.post("/hooks/gitea", status_code=202)
async def receive_gitea_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            parsed_content_length = int(content_length)
            if parsed_content_length < 0:
                return webhook_response("Invalid content length.", 400)
            if parsed_content_length > MAX_WEBHOOK_BYTES:
                return webhook_response("Payload too large.", 413)
        except ValueError:
            return webhook_response("Invalid content length.", 400)

    payload_buffer = bytearray()
    async for chunk in request.stream():
        if len(payload_buffer) + len(chunk) > MAX_WEBHOOK_BYTES:
            return webhook_response("Payload too large.", 413)
        payload_buffer.extend(chunk)
    body = bytes(payload_buffer)

    supplied_signature = request.headers.get("x-gitea-signature", "")
    expected_signature = hmac.new(
        get_gitea_webhook_secret().encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature.lower(), expected_signature):
        return webhook_response("Invalid signature.", 401)

    if request.headers.get("x-gitea-event", "").lower() != "push":
        return webhook_response("Event ignored.", 202)

    delivery_id = (
        request.headers.get("x-gitea-delivery")
        or request.headers.get("x-gogs-delivery")
        or ""
    )
    if not delivery_id or len(delivery_id) > 255:
        return webhook_response("Invalid delivery ID.", 400)

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return webhook_response("Invalid JSON payload.", 400)

    repository = payload.get("repository") if isinstance(payload, dict) else None
    repository_path = (
        repository.get("full_name") if isinstance(repository, dict) else None
    )
    ref = payload.get("ref") if isinstance(payload, dict) else None
    commit_sha = payload.get("after") if isinstance(payload, dict) else None
    if (
        not isinstance(repository_path, str)
        or not repository_path
        or len(repository_path) > 255
        or not isinstance(ref, str)
        or not ref
        or len(ref) > 512
        or not isinstance(commit_sha, str)
        or not COMMIT_SHA_PATTERN.fullmatch(commit_sha)
    ):
        return webhook_response("Invalid push payload.", 400)

    commit_sha = commit_sha.lower()
    if set(commit_sha) == {"0"}:
        return webhook_response("Branch deletion ignored.", 202)
    if session.scalar(select(RepositoryEvent.id).where(RepositoryEvent.delivery_id == delivery_id)):
        return webhook_response("Delivery already received.", 202)
    push_key = hashlib.sha256(json.dumps([repository_path, ref, commit_sha]).encode()).hexdigest()
    service = session.scalar(
        select(Service).where(
            or_(
                Service.repository_path == repository_path,
                Service.jury_repository_path == repository_path,
            )
        )
    )
    if service is None:
        participant_service = session.scalar(
            select(ParticipantService).where(
                or_(
                    ParticipantService.attack_repository_path == repository_path,
                    ParticipantService.defense_repository_path == repository_path,
                )
            )
        )
        if participant_service is None:
            return webhook_response("Repository ignored.", 202)
        # Shared config/service locks serialize admission against admin close,
        # without serializing independent PostgreSQL repositories with each other.
        if session.bind.dialect.name == "sqlite":
            session.execute(update(InstanceConfig).where(InstanceConfig.id == 1).values(
                site_name=InstanceConfig.site_name,
            ))
        config = session.scalar(select(InstanceConfig).where(InstanceConfig.id == 1)
                                .with_for_update(read=True).execution_options(populate_existing=True))
        service = session.scalar(select(Service).where(Service.id == participant_service.service_id)
                                 .with_for_update(read=True).execution_options(populate_existing=True))
        if service is None:
            return webhook_response("Repository ignored.", 202)
        if ref != f"refs/heads/{service.default_branch}":
            return webhook_response("Branch ignored.", 202)
        if service.status != ServiceStatus.ACTIVE or service.runtime_status != ServiceRunStatus.PASSED:
            return webhook_response("Participant push ignored: service is closed.", 202)
        participant_service = session.scalar(select(ParticipantService).where(
            ParticipantService.id == participant_service.id,
        ).with_for_update().execution_options(populate_existing=True))
        if participant_service.service_id != service.id or repository_path not in {
            participant_service.attack_repository_path, participant_service.defense_repository_path,
        }:
            return webhook_response("Repository is no longer managed by this assignment.", 202)
        if repository_path == participant_service.defense_repository_path and not participant_service.defense_unlocked:
            return webhook_response("Participant push ignored: defense is locked.", 202)
        rejected = await verify_head(session, repository_path, service.default_branch, commit_sha)
        if rejected is not None:
            return rejected
        # Test the deadline after waiting for locks and verifying HEAD.
        admitted_at = datetime.now(timezone.utc)
        if contest_state(config, now=admitted_at) != STARTED:
            return webhook_response(
                "Participant push ignored: the contest is not running.",
                202,
            )
        pending_events = session.scalar(
            select(func.count())
            .select_from(RepositoryEvent)
            .where(
                RepositoryEvent.repository_path == repository_path,
                RepositoryEvent.status == RepositoryEventStatus.PENDING,
            )
        )
        if (pending_events or 0) >= MAX_PENDING_EVENTS_PER_REPOSITORY:
            return webhook_response(
                "Push ignored: previous pushes are still being checked.",
                202,
            )
        if repository_path == participant_service.attack_repository_path:
            participant_service.attack_status = ParticipantRepositoryStatus.RUNNING
            participant_service.attack_message = "Push queued for checking."
        else:
            participant_service.defense_status = ParticipantRepositoryStatus.RUNNING
            participant_service.defense_message = "Push queued for checking."
        session.add(RepositoryEvent(
            delivery_id=delivery_id, repository_path=repository_path, ref=ref,
            commit_sha=commit_sha, push_key=push_key,
            admitted_at=admitted_at, received_at=admitted_at,
            admission_json=snapshot_admission(session, service, participant_service),
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return webhook_response("Delivery already received.", 202)
        return webhook_response("Participant push queued.", 202)
    if ref != f"refs/heads/{service.default_branch}":
        return webhook_response("Branch ignored.", 202)

    session.execute(update(Service).where(Service.id == service.id).values(
        repository_generation=Service.repository_generation,
    ))
    session.refresh(service)
    if ref != f"refs/heads/{service.default_branch}":
        return webhook_response("Branch ignored.", 202)
    rejected = await verify_head(session, repository_path, service.default_branch, commit_sha)
    if rejected is not None:
        return rejected

    session.add(
        RepositoryEvent(
            delivery_id=delivery_id,
            repository_path=repository_path,
            ref=ref,
            commit_sha=commit_sha,
            push_key=push_key,
        )
    )
    session.execute(
        update(Service)
        .where(Service.id == service.id)
        .values(
            status=ServiceStatus.DRAFT,
            validation_status=ServiceValidationStatus.PENDING,
            validation_message=(
                f"New push received from {repository_path}; validation required."
            ),
            repository_generation=Service.repository_generation + 1,
            validated_source_commit=None,
            validated_jury_commit=None,
            container_port=None,
            healthcheck_path=None,
            validated_at=None,
            runtime_status=ServiceRunStatus.PENDING,
            runtime_message="Push queued for an automatic runtime check.",
            runtime_log="",
            runtime_matches=0,
            runtime_source_commit=None,
            runtime_jury_commit=None,
            runtime_checked_at=None,
        )
        .execution_options(synchronize_session=False)
    )

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return webhook_response("Delivery already received.", 202)

    return webhook_response("Push queued.", 202)
