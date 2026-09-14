import hashlib
import hmac
import json
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from selfad.database import get_session
from selfad.models import (
    RepositoryEvent,
    ParticipantService,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
)
from selfad.settings import get_gitea_webhook_secret


router = APIRouter()

MAX_WEBHOOK_BYTES = 1_048_576
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


def webhook_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status_code)


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
        service = session.get(Service, participant_service.service_id)
        if service is None:
            return webhook_response("Repository ignored.", 202)
        if ref != f"refs/heads/{service.default_branch}":
            return webhook_response("Branch ignored.", 202)
        session.add(RepositoryEvent(delivery_id=delivery_id, repository_path=repository_path, ref=ref, commit_sha=commit_sha.lower()))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return webhook_response("Delivery already received.", 202)
        return webhook_response("Participant push queued.", 202)
    if ref != f"refs/heads/{service.default_branch}":
        return webhook_response("Branch ignored.", 202)

    session.add(
        RepositoryEvent(
            delivery_id=delivery_id,
            repository_path=repository_path,
            ref=ref,
            commit_sha=commit_sha.lower(),
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
