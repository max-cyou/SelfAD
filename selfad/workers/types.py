from dataclasses import dataclass


@dataclass(frozen=True)
class RepositoryBatch:
    event_ids: tuple[int, ...]
    service_id: int
    repository_generation: int
    participant_service_id: int | None = None
    repository_path: str | None = None
    commit_sha: str | None = None
    source_commit: str | None = None
    jury_commit: str | None = None
