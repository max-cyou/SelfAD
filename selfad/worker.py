import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import update

from selfad.database import SessionLocal
from selfad.gitea import GiteaError, get_branch_commit
from selfad.models import (
    RepositoryEventStatus,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
)
from selfad.runner import (
    RunnerError,
    run_service_runtime_check,
)
from selfad.service_contract import validate_service_contract
from selfad.settings import get_gitea_settings
from selfad.workers.participant import (
    _process_participant_batch,
)
from selfad.workers.queue import (
    _batch_is_superseded as _batch_is_superseded,
)
from selfad.workers.queue import (
    _claim_repository_batch,
    _finish_events,
)
from selfad.workers.queue import (
    recover_interrupted_repository_events as recover_interrupted_repository_events,
)
from selfad.workers.types import (
    RepositoryBatch,
)

logger = logging.getLogger(__name__)


async def run_repository_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(process_next_repository_batch)
        except Exception:
            logger.exception("Repository worker failed unexpectedly.")
            processed = False
        if processed:
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=2)
        except TimeoutError:
            pass


def process_next_repository_batch() -> bool:
    batch = _claim_repository_batch()
    if batch is None:
        return False
    if batch.participant_service_id is not None:
        return _process_participant_batch(batch)

    with SessionLocal() as session:
        service = session.get(Service, batch.service_id)
        if service is None:
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                "Service no longer exists.",
            )
            session.commit()
            return True
        repository_path = service.repository_path
        jury_repository_path = service.jury_repository_path
        default_branch = service.default_branch

    if not repository_path or not jury_repository_path:
        _finish_batch_as_failure(
            batch,
            "The service and jury repositories must be provisioned.",
        )
        return True

    settings = get_gitea_settings()
    try:
        source_commit = batch.source_commit or get_branch_commit(
            settings,
            repository_path,
            branch=default_branch,
        )
        jury_commit = batch.jury_commit or get_branch_commit(
            settings,
            jury_repository_path,
            branch=default_branch,
        )
        contract = validate_service_contract(
            settings,
            repository_path=repository_path,
            jury_repository_path=jury_repository_path,
            default_branch=default_branch,
            source_commit=source_commit,
            jury_commit=jury_commit,
        )
    except GiteaError as error:
        _finish_batch_as_failure(batch, str(error))
        return True

    if not contract.valid:
        with SessionLocal() as session:
            execution = session.execute(
                update(Service)
                .where(
                    Service.id == batch.service_id,
                    Service.repository_generation == batch.repository_generation,
                )
                .values(
                    status=ServiceStatus.DRAFT,
                    validation_status=ServiceValidationStatus.INVALID,
                    validation_message=contract.message,
                    validated_source_commit=contract.source_commit,
                    validated_jury_commit=contract.jury_commit,
                    container_port=None,
                    healthcheck_path=None,
                    validated_at=datetime.now(timezone.utc),
                    runtime_status=ServiceRunStatus.FAILED,
                    runtime_message="Runtime check skipped: repository contract is invalid.",
                    runtime_log="",
                    runtime_matches=0,
                    runtime_source_commit=None,
                    runtime_jury_commit=None,
                    runtime_checked_at=datetime.now(timezone.utc),
                )
            )
            message = contract.message if execution.rowcount == 1 else "Superseded by a newer push."
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                message,
            )
            session.commit()
        return True

    with SessionLocal() as session:
        execution = session.execute(
            update(Service)
            .where(
                Service.id == batch.service_id,
                Service.repository_generation == batch.repository_generation,
            )
            .values(
                validation_status=ServiceValidationStatus.VALID,
                validation_message=contract.message,
                validated_source_commit=contract.source_commit,
                validated_jury_commit=contract.jury_commit,
                container_port=contract.container_port,
                healthcheck_path=contract.healthcheck_path,
                validated_at=datetime.now(timezone.utc),
                runtime_status=ServiceRunStatus.RUNNING,
                runtime_message="Building and checking the service.",
                runtime_log="",
                runtime_matches=0,
                runtime_source_commit=contract.source_commit,
                runtime_jury_commit=contract.jury_commit,
                runtime_checked_at=None,
            )
        )
        if execution.rowcount != 1:
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                "Superseded by a newer push.",
            )
            session.commit()
            return True
        session.commit()

    try:
        runtime = run_service_runtime_check(
            settings,
            repository_path=repository_path,
            jury_repository_path=jury_repository_path,
            contract=contract,
            cache_service_image=True,
        )
    except (GiteaError, RunnerError) as error:
        _finish_batch_as_failure(batch, str(error), contract_is_valid=True)
        return True

    with SessionLocal() as session:
        execution = session.execute(
            update(Service)
            .where(
                Service.id == batch.service_id,
                Service.repository_generation == batch.repository_generation,
            )
            .values(
                status=ServiceStatus.DRAFT,
                runtime_status=(
                    ServiceRunStatus.PASSED if runtime.passed else ServiceRunStatus.FAILED
                ),
                runtime_message=runtime.message,
                runtime_log=runtime.log,
                runtime_matches=runtime.matched_flags,
                runtime_source_commit=contract.source_commit,
                runtime_jury_commit=contract.jury_commit,
                runtime_checked_at=datetime.now(timezone.utc),
            )
        )
        message = runtime.message if execution.rowcount == 1 else "Superseded by a newer push."
        _finish_events(
            session,
            batch.event_ids,
            RepositoryEventStatus.DONE,
            message,
        )
        session.commit()
    return True


def _finish_batch_as_failure(
    batch: RepositoryBatch,
    message: str,
    *,
    contract_is_valid: bool = False,
) -> None:
    with SessionLocal() as session:
        values: dict[str, object] = {
            "status": ServiceStatus.DRAFT,
            "runtime_status": ServiceRunStatus.FAILED,
            "runtime_message": message,
            "runtime_log": "",
            "runtime_matches": 0,
            "runtime_checked_at": datetime.now(timezone.utc),
        }
        if not contract_is_valid:
            values.update(
                validation_status=ServiceValidationStatus.PENDING,
                validation_message=message,
            )
        execution = session.execute(
            update(Service)
            .where(
                Service.id == batch.service_id,
                Service.repository_generation == batch.repository_generation,
            )
            .values(**values)
        )
        event_message = message if execution.rowcount == 1 else "Superseded by a newer push."
        _finish_events(
            session,
            batch.event_ids,
            RepositoryEventStatus.FAILED,
            event_message,
        )
        session.commit()
