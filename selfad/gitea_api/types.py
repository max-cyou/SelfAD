from dataclasses import dataclass

GITEA_REPOSITORY_OWNER = "root"


class GiteaError(Exception):
    pass


class GiteaRepositoryNotFound(GiteaError):
    pass


class GiteaUnavailable(GiteaError):
    pass


class GiteaConflict(GiteaError):
    pass


class GiteaFileTooLarge(GiteaError):
    pass


class _GiteaRequestError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


@dataclass(frozen=True)
class GiteaRepository:
    id: int
    path: str
    default_branch: str | None
    empty: bool


@dataclass(frozen=True)
class GiteaUser:
    id: int
    username: str


@dataclass(frozen=True)
class ProvisionedService:
    service_repository: GiteaRepository
    jury_repository: GiteaRepository
    ssh_key_id: int


@dataclass(frozen=True)
class GiteaRepositoryFile:
    path: str
    sha: str
    size: int


@dataclass(frozen=True)
class GiteaFileContent:
    path: str
    sha: str
    content: bytes


@dataclass(frozen=True)
class GiteaFileChange:
    operation: str
    path: str
    content: bytes | None = None
    sha: str | None = None
