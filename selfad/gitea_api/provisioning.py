from selfad.gitea_api.files import (
    create_repository_file,
)
from selfad.gitea_api.repositories import (
    add_repository_collaborator,
    create_repository,
    delete_repository,
    ensure_repository_webhook,
)
from selfad.gitea_api.types import (
    GITEA_REPOSITORY_OWNER,
    GiteaError,
    GiteaRepository,
    ProvisionedService,
)
from selfad.gitea_api.users import (
    ensure_ssh_key,
)
from selfad.repository_readmes import jury_readme, organizer_readme
from selfad.settings import GiteaSettings


def provision_service(
    settings: GiteaSettings,
    *,
    name: str,
    slug: str,
    description: str,
    default_branch: str,
    ssh_public_key: str,
    organizer_username: str,
    webhook_secret: str,
) -> ProvisionedService:
    ssh_key_id = ensure_ssh_key(
        settings,
        ssh_public_key,
        username=organizer_username,
    )
    service_repository = create_repository(
        settings,
        path=slug,
        description=description,
        default_branch=default_branch,
    )
    jury_repository: GiteaRepository | None = None
    try:
        create_repository_file(
            settings,
            service_repository.path,
            "README.md",
            content=organizer_readme(name, default_branch),
            branch=default_branch,
            message="Add service author instructions",
        )
        jury_repository = create_repository(
            settings,
            path=f"{slug}-jury",
            description=f"Private SelfAD jury files for {name}.",
            default_branch=default_branch,
        )
        if organizer_username != GITEA_REPOSITORY_OWNER:
            for repository_path in (
                service_repository.path,
                jury_repository.path,
            ):
                add_repository_collaborator(
                    settings,
                    repository_path,
                    username=organizer_username,
                    permission="write",
                )
        create_repository_file(
            settings,
            jury_repository.path,
            "README.md",
            content=jury_readme(name, default_branch),
            branch=default_branch,
            message="Add jury instructions",
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
