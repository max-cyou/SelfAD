"""Public API; implementation is organized by responsibility."""

from selfad.gitea_api.client import (
    _parse_repository as _parse_repository,
)
from selfad.gitea_api.client import (
    _request as _request,
)
from selfad.gitea_api.files import (
    change_repository_files as change_repository_files,
)
from selfad.gitea_api.files import (
    create_repository_file as create_repository_file,
)
from selfad.gitea_api.files import (
    download_repository_archive as download_repository_archive,
)
from selfad.gitea_api.files import (
    get_repository_file as get_repository_file,
)
from selfad.gitea_api.files import (
    get_repository_file_content as get_repository_file_content,
)
from selfad.gitea_api.files import (
    list_repository_file_entries as list_repository_file_entries,
)
from selfad.gitea_api.files import (
    list_repository_files as list_repository_files,
)
from selfad.gitea_api.files import (
    replace_repository_file as replace_repository_file,
)
from selfad.gitea_api.files import (
    repository_file_exists as repository_file_exists,
)
from selfad.gitea_api.provisioning import (
    provision_service as provision_service,
)
from selfad.gitea_api.repositories import (
    add_repository_collaborator as add_repository_collaborator,
)
from selfad.gitea_api.repositories import (
    create_repository as create_repository,
)
from selfad.gitea_api.repositories import (
    delete_repository as delete_repository,
)
from selfad.gitea_api.repositories import (
    ensure_repository_webhook as ensure_repository_webhook,
)
from selfad.gitea_api.repositories import (
    get_branch_commit as get_branch_commit,
)
from selfad.gitea_api.repositories import (
    get_repository as get_repository,
)
from selfad.gitea_api.repositories import (
    remove_repository_collaborator as remove_repository_collaborator,
)
from selfad.gitea_api.types import (
    GITEA_REPOSITORY_OWNER as GITEA_REPOSITORY_OWNER,
)
from selfad.gitea_api.types import (
    GiteaConflict as GiteaConflict,
)
from selfad.gitea_api.types import (
    GiteaError as GiteaError,
)
from selfad.gitea_api.types import (
    GiteaFileChange as GiteaFileChange,
)
from selfad.gitea_api.types import (
    GiteaFileContent as GiteaFileContent,
)
from selfad.gitea_api.types import (
    GiteaFileTooLarge as GiteaFileTooLarge,
)
from selfad.gitea_api.types import (
    GiteaRepository as GiteaRepository,
)
from selfad.gitea_api.types import (
    GiteaRepositoryFile as GiteaRepositoryFile,
)
from selfad.gitea_api.types import (
    GiteaRepositoryNotFound as GiteaRepositoryNotFound,
)
from selfad.gitea_api.types import (
    GiteaUnavailable as GiteaUnavailable,
)
from selfad.gitea_api.types import (
    GiteaUser as GiteaUser,
)
from selfad.gitea_api.types import (
    ProvisionedService as ProvisionedService,
)
from selfad.gitea_api.users import (
    add_user_ssh_key as add_user_ssh_key,
)
from selfad.gitea_api.users import (
    authenticate_gitea_user as authenticate_gitea_user,
)
from selfad.gitea_api.users import (
    create_gitea_user as create_gitea_user,
)
from selfad.gitea_api.users import (
    delete_gitea_user as delete_gitea_user,
)
from selfad.gitea_api.users import (
    delete_user_ssh_key as delete_user_ssh_key,
)
from selfad.gitea_api.users import (
    ensure_ssh_key as ensure_ssh_key,
)
from selfad.gitea_api.users import (
    get_authenticated_user as get_authenticated_user,
)
from selfad.gitea_api.users import (
    gitea_username_exists as gitea_username_exists,
)
from selfad.gitea_api.users import (
    update_gitea_user as update_gitea_user,
)
