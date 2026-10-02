import hashlib

from selfad.gitea import (
    GiteaError,
    add_repository_collaborator,
    create_repository,
    create_repository_file,
    delete_repository,
    ensure_repository_webhook,
    get_repository_file,
    list_repository_files,
)
from selfad.models import ParticipantService, Service, User
from selfad.repository_readmes import attack_readme
from selfad.settings import GiteaSettings

ATTACK_DOCKERFILE = b'FROM python:3.13-alpine\nWORKDIR /workspace\nCMD ["python", "exploit.py"]\n'
ATTACK_EXPLOIT = b"""import os
import sys

# SELFAD_TARGET is a complete in-network URL, e.g. http://target:8080.
target = os.environ.get("SELFAD_TARGET") or sys.argv[1]
target = target.rstrip("/")

# Example when the organizer provides the requests package:
# import requests
# response = requests.get(f"{target}/your-endpoint", timeout=5)

# Print only recovered flags: one exact 32-character A-Z/0-9 flag per line.
"""


def provision_participant_service(
    settings: GiteaSettings,
    *,
    service: Service,
    user: User,
    attack_requirements: str,
    allow_user_attack_requirements: bool,
    webhook_secret: str,
) -> ParticipantService:
    if (
        not user.gitea_username
        or not service.runtime_source_commit
        or not service.repository_path
        or not service.jury_repository_path
    ):
        raise GiteaError("Participant or service is not ready for provisioning.")

    attack_name = f"{service.slug}-{user.username}-attack"
    defense_name = f"{service.slug}-{user.username}-defense"
    attack = None
    defense = None
    try:
        attack = create_repository(
            settings,
            path=attack_name,
            description=f"SelfAD attack repository for {service.name}.",
            default_branch=service.default_branch,
        )
        defense = create_repository(
            settings,
            path=defense_name,
            description=f"SelfAD defense repository for {service.name}.",
            default_branch=service.default_branch,
        )
        service_port = service.container_port
        if service_port is None:
            raise GiteaError("Validated service port is unavailable.")
        source_files = list_repository_files(
            settings,
            service.repository_path,
            ref=service.runtime_source_commit,
        )
        attack_files = [
            (
                "README.md",
                attack_readme(
                    service.name,
                    service.default_branch,
                    service_port,
                    attack_requirements,
                    allow_user_attack_requirements,
                ),
            ),
            ("Dockerfile", ATTACK_DOCKERFILE),
            ("exploit.py", ATTACK_EXPLOIT),
        ]
        for path, content in attack_files:
            create_repository_file(
                settings,
                attack.path,
                path,
                content=content,
                branch=service.default_branch,
                message="Initialize SelfAD attack repository",
            )

        for path in source_files:
            if path == "README.md":
                continue
            content = get_repository_file(
                settings,
                service.repository_path,
                path,
                ref=service.runtime_source_commit,
                max_bytes=256 * 1024,
            )
            if content is None:
                raise GiteaError(f"Could not copy {path} into the defense repository.")
            create_repository_file(
                settings,
                defense.path,
                path,
                content=content,
                branch=service.default_branch,
                message="Initialize SelfAD defense repository",
            )

        source_dockerfile = get_repository_file(
            settings, service.repository_path, "Dockerfile", ref=service.runtime_source_commit
        )
        if source_dockerfile is None:
            raise GiteaError("The service Dockerfile is unavailable.")
        for repository_path, permission in (
            (service.repository_path, "read"),
            (attack.path, "write"),
            (defense.path, "write"),
        ):
            owner = repository_path.split("/", 1)[0]
            if owner != user.gitea_username:
                add_repository_collaborator(
                    settings, repository_path, username=user.gitea_username, permission=permission
                )
        for repository_path in (attack.path, defense.path):
            ensure_repository_webhook(
                settings,
                repository_path,
                secret=webhook_secret,
                branch_filter=service.default_branch,
            )
    except GiteaError:
        for repository in (defense, attack):
            if repository is None:
                continue
            try:
                delete_repository(settings, repository.path)
            except GiteaError:
                pass
        raise

    return ParticipantService(
        service_id=service.id,
        user_id=user.id,
        attack_repository_id=attack.id,
        attack_repository_path=attack.path,
        defense_repository_id=defense.id,
        defense_repository_path=defense.path,
        attack_dockerfile_sha=hashlib.sha256(ATTACK_DOCKERFILE).hexdigest(),
        defense_dockerfile_sha=hashlib.sha256(source_dockerfile).hexdigest(),
    )
