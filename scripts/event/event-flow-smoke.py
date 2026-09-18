#!/usr/bin/env python3
"""Exercise signed push intake, queue processing, scoring and defense unlock.

Run next to ``runner-smoke.py`` inside the control-plane container. All
repositories, rows and temporary contest state are removed afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from urllib.request import Request, urlopen

from sqlalchemy import delete

from runner_smoke import (
    ATTACK_DOCKERFILE,
    BRANCH,
    CHECKER,
    EXPLOIT,
    INJECT,
    PATCHED_APP,
    SERVICE_CONFIG,
    SERVICE_DOCKERFILE,
    VULNERABLE_APP,
    seed,
)
from selfad.database import SessionLocal
from selfad.gitea import delete_repository, get_branch_commit
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
    SubmissionAttempt,
    User,
)
from selfad.runner import run_service_runtime_check
from selfad.security import hash_password
from selfad.service_contract import validate_service_contract
from selfad.settings import get_gitea_settings, get_gitea_webhook_secret


def send_push(repository_path: str, commit: str) -> str:
    delivery = uuid.uuid4().hex
    body = json.dumps(
        {
            "repository": {"full_name": repository_path},
            "ref": f"refs/heads/{BRANCH}",
            "after": commit,
        }
    ).encode()
    signature = hmac.new(
        get_gitea_webhook_secret().encode(), body, hashlib.sha256
    ).hexdigest()
    request = Request(
        "http://127.0.0.1:8000/hooks/gitea",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": delivery,
            "X-Gitea-Signature": signature,
        },
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 202:
            raise RuntimeError(f"webhook returned HTTP {response.status}")
    return delivery


def wait_for_delivery(delivery: str) -> None:
    for _ in range(90):
        with SessionLocal() as session:
            event = session.query(RepositoryEvent).filter_by(delivery_id=delivery).one_or_none()
            if event and event.status in {
                RepositoryEventStatus.DONE,
                RepositoryEventStatus.FAILED,
            }:
                if event.status != RepositoryEventStatus.DONE:
                    raise RuntimeError(f"event failed: {event.message}")
                return
        time.sleep(1)
    raise RuntimeError("timed out waiting for repository worker")


def main() -> int:
    settings = get_gitea_settings()
    suffix = secrets.token_hex(5)
    repositories: list[str] = []
    service_id: int | None = None
    user_id: int | None = None
    player_id: int | None = None
    original_config: tuple[bool, bool, object] | None = None
    created_config = False
    try:
        source = seed(settings, f"selfad-event-{suffix}-service", {
            "Dockerfile": SERVICE_DOCKERFILE,
            "selfad.yml": SERVICE_CONFIG,
            "app.py": VULNERABLE_APP,
        })
        jury = seed(settings, f"selfad-event-{suffix}-jury", {
            "inject.py": INJECT, "exploit.py": EXPLOIT, "checker.py": CHECKER,
        })
        attack = seed(settings, f"selfad-event-{suffix}-attack", {
            "Dockerfile": ATTACK_DOCKERFILE, "exploit.py": EXPLOIT,
        })
        defense = seed(settings, f"selfad-event-{suffix}-defense", {
            "Dockerfile": SERVICE_DOCKERFILE,
            "selfad.yml": SERVICE_CONFIG,
            "app.py": PATCHED_APP,
        })
        repositories.extend((source, jury, attack, defense))

        contract = validate_service_contract(
            settings, repository_path=source, jury_repository_path=jury,
            default_branch=BRANCH,
        )
        if not contract.valid:
            raise RuntimeError(contract.message)
        canonical = run_service_runtime_check(
            settings, repository_path=source, jury_repository_path=jury,
            contract=contract,
            cache_service_image=True,
        )
        if not canonical.passed:
            raise RuntimeError(f"canonical runtime failed: {canonical.message}")

        with SessionLocal() as session:
            config = session.get(InstanceConfig, 1)
            if config is None:
                config = InstanceConfig(id=1, contest_started=True)
                session.add(config)
                created_config = True
            else:
                original_config = (
                    config.contest_started, config.contest_ended,
                    config.contest_starts_at,
                )
                config.contest_started = True
                config.contest_ended = False
                config.contest_starts_at = None
            service = Service(
                name="Event smoke",
                slug=f"event-{suffix}",
                repository_path=source,
                jury_repository_path=jury,
                default_branch=BRANCH,
                status=ServiceStatus.ACTIVE,
                validation_status=ServiceValidationStatus.VALID,
                validation_message=contract.message,
                validated_source_commit=contract.source_commit,
                validated_jury_commit=contract.jury_commit,
                container_port=contract.container_port,
                healthcheck_path=contract.healthcheck_path,
                runtime_status=ServiceRunStatus.PASSED,
                runtime_message=canonical.message,
                runtime_matches=canonical.matched_flags,
                runtime_source_commit=contract.source_commit,
                runtime_jury_commit=contract.jury_commit,
            )
            user = User(
                username=f"event{suffix}",
                email=f"event-{suffix}@example.invalid",
                password_hash=hash_password("event-smoke-password"),
            )
            session.add_all((service, user))
            session.flush()
            service_id, user_id = service.id, user.id
            player = ParticipantService(
                service_id=service.id,
                user_id=user.id,
                attack_repository_path=attack,
                defense_repository_path=defense,
                attack_dockerfile_sha=hashlib.sha256(ATTACK_DOCKERFILE).hexdigest(),
                defense_dockerfile_sha=hashlib.sha256(SERVICE_DOCKERFILE).hexdigest(),
            )
            session.add(player)
            session.flush()
            player_id = player.id
            session.commit()

        attack_delivery = send_push(
            attack, get_branch_commit(settings, attack, branch=BRANCH)
        )
        wait_for_delivery(attack_delivery)
        with SessionLocal() as session:
            player = session.get(ParticipantService, player_id)
            if not player or not player.defense_unlocked or player.attack_score <= 0:
                raise RuntimeError("attack webhook did not unlock defense and award points")

        defense_delivery = send_push(
            defense, get_branch_commit(settings, defense, branch=BRANCH)
        )
        wait_for_delivery(defense_delivery)
        with SessionLocal() as session:
            player = session.get(ParticipantService, player_id)
            if not player or player.defense_status != ParticipantRepositoryStatus.PASSED:
                raise RuntimeError("defense webhook did not pass")
            if player.defense_score <= 0:
                raise RuntimeError("defense webhook did not award points")
        print("event flow smoke passed: signed webhooks, queue, scores and defense unlock")
        return 0
    finally:
        with SessionLocal() as session:
            if player_id is not None:
                session.execute(delete(SubmissionAttempt).where(SubmissionAttempt.participant_service_id == player_id))
                session.execute(delete(ParticipantService).where(ParticipantService.id == player_id))
            if service_id is not None:
                session.execute(delete(RepositoryEvent).where(RepositoryEvent.repository_path.in_(repositories)))
                session.execute(delete(Service).where(Service.id == service_id))
            if user_id is not None:
                session.execute(delete(User).where(User.id == user_id))
            config = session.get(InstanceConfig, 1)
            if created_config and config is not None:
                session.delete(config)
            elif original_config and config is not None:
                config.contest_started, config.contest_ended, config.contest_starts_at = original_config
            session.commit()
        for repository in reversed(repositories):
            try:
                delete_repository(settings, repository)
            except Exception as error:
                print(f"cleanup warning for {repository}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
