import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_, select, update

from selfad.contest import STARTED, contest_state, start_contest_if_due
from selfad.database import SessionLocal
from selfad.gitea import GiteaError
from selfad.models import (
    InstanceConfig,
    RepositoryEvent,
    RepositoryEventStatus,
    ParticipantRepositoryStatus,
    ParticipantService,
    Service,
    ServiceRunStatus,
    ServiceStatus,
    ServiceValidationStatus,
)
from selfad.runner import (
    MAX_REQUIREMENTS_BYTES,
    RunnerError,
    build_attack_runtime_requirements,
    run_service_runtime_check,
)
from selfad.scoring import (
    ATTACK,
    DEFENSE,
    get_scoring_settings,
    record_submission_score,
    score_message,
)
from selfad.service_contract import ServiceContractResult, validate_service_contract
from selfad.settings import get_gitea_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositoryBatch:
    event_ids: tuple[int, ...]
    service_id: int
    repository_generation: int
    participant_service_id: int | None = None
    repository_path: str | None = None
    commit_sha: str | None = None


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
        contract = validate_service_contract(
            settings,
            repository_path=repository_path,
            jury_repository_path=jury_repository_path,
            default_branch=default_branch,
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
            message = (
                contract.message
                if execution.rowcount == 1
                else "Superseded by a newer push."
            )
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
                    ServiceRunStatus.PASSED
                    if runtime.passed
                    else ServiceRunStatus.FAILED
                ),
                runtime_message=runtime.message,
                runtime_log=runtime.log,
                runtime_matches=runtime.matched_flags,
                runtime_source_commit=contract.source_commit,
                runtime_jury_commit=contract.jury_commit,
                runtime_checked_at=datetime.now(timezone.utc),
            )
        )
        message = (
            runtime.message
            if execution.rowcount == 1
            else "Superseded by a newer push."
        )
        _finish_events(
            session,
            batch.event_ids,
            RepositoryEventStatus.DONE,
            message,
        )
        session.commit()
    return True


def _claim_repository_batch() -> RepositoryBatch | None:
    with SessionLocal() as session:
        event = session.scalar(
            select(RepositoryEvent)
            .where(RepositoryEvent.status == RepositoryEventStatus.PENDING)
            .order_by(RepositoryEvent.id)
            .limit(1)
        )
        if event is None:
            return None

        service = session.scalar(
            select(Service).where(
                or_(
                    Service.repository_path == event.repository_path,
                    Service.jury_repository_path == event.repository_path,
                )
            )
        )
        if service is None:
            participant_service = session.scalar(
                select(ParticipantService).where(
                    or_(
                        ParticipantService.attack_repository_path == event.repository_path,
                        ParticipantService.defense_repository_path == event.repository_path,
                    )
                )
            )
            if participant_service is None:
                event.status = RepositoryEventStatus.DONE
                event.message = "Repository is no longer managed by SelfAD."
                event.processed_at = datetime.now(timezone.utc)
                session.commit()
                return RepositoryBatch((event.id,), -1, -1)
            service = session.get(Service, participant_service.service_id)
            if service is None:
                event.status = RepositoryEventStatus.DONE
                event.message = "Service no longer exists."
                event.processed_at = datetime.now(timezone.utc)
                session.commit()
                return RepositoryBatch((event.id,), -1, -1)
            event_ids = tuple(session.scalars(select(RepositoryEvent.id).where(RepositoryEvent.status == RepositoryEventStatus.PENDING, RepositoryEvent.repository_path == event.repository_path)).all())
            latest_event = session.scalar(select(RepositoryEvent).where(RepositoryEvent.id.in_(event_ids)).order_by(RepositoryEvent.id.desc()))
            session.execute(update(RepositoryEvent).where(RepositoryEvent.id.in_(event_ids)).values(attempts=RepositoryEvent.attempts + 1, message="Participant check started."))
            if event.repository_path == participant_service.attack_repository_path:
                participant_service.attack_status = ParticipantRepositoryStatus.RUNNING
                participant_service.attack_message = "Checking the latest attack push."
            else:
                participant_service.defense_status = ParticipantRepositoryStatus.RUNNING
                participant_service.defense_message = "Checking the latest defense push."
            session.commit()
            return RepositoryBatch(event_ids, service.id, service.repository_generation, participant_service.id, event.repository_path, latest_event.commit_sha if latest_event else None)

        event_ids = tuple(
            session.scalars(
                select(RepositoryEvent.id).where(
                    RepositoryEvent.status == RepositoryEventStatus.PENDING,
                    or_(
                        RepositoryEvent.repository_path == service.repository_path,
                        RepositoryEvent.repository_path
                        == service.jury_repository_path,
                    ),
                )
            ).all()
        )
        session.execute(
            update(RepositoryEvent)
            .where(RepositoryEvent.id.in_(event_ids))
            .values(
                attempts=RepositoryEvent.attempts + 1,
                message="Repository check started.",
            )
        )
        session.commit()
        return RepositoryBatch(
            event_ids,
            service.id,
            service.repository_generation,
        )


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
        event_message = (
            message
            if execution.rowcount == 1
            else "Superseded by a newer push."
        )
        _finish_events(
            session,
            batch.event_ids,
            RepositoryEventStatus.FAILED,
            event_message,
        )
        session.commit()


def _process_participant_batch(batch: RepositoryBatch) -> bool:
    with SessionLocal() as session:
        player = session.get(ParticipantService, batch.participant_service_id)
        service = session.get(Service, batch.service_id)
        if player is None or service is None or not batch.repository_path or not batch.commit_sha:
            _finish_events(session, batch.event_ids, RepositoryEventStatus.FAILED, "Participant repository is unavailable.")
            session.commit()
            return True
        is_attack = batch.repository_path == player.attack_repository_path
        config = session.get(InstanceConfig, 1)
        if start_contest_if_due(config):
            session.commit()
        if contest_state(config) != STARTED:
            message = "Submission ignored: the contest is not running."
            if is_attack:
                player.attack_status = ParticipantRepositoryStatus.FAILED
                player.attack_message = message
            else:
                player.defense_status = ParticipantRepositoryStatus.FAILED
                player.defense_message = message
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                message,
            )
            session.commit()
            return True
        scoring_settings = get_scoring_settings(session)
        expected_dockerfile = player.attack_dockerfile_sha if is_attack else player.defense_dockerfile_sha
        repository_path = batch.repository_path
        jury_path = service.jury_repository_path
        if not jury_path:
            _finish_events(session, batch.event_ids, RepositoryEventStatus.FAILED, "Jury repository is unavailable.")
            session.commit()
            return True
        if not is_attack and not player.defense_unlocked:
            player.defense_status = ParticipantRepositoryStatus.FAILED
            player.defense_message = "Defense unlocks after a successful attack."
            _finish_events(session, batch.event_ids, RepositoryEventStatus.FAILED, player.defense_message)
            session.commit()
            return True
        fixed_attack_requirements = (
            scoring_settings.attack_requirements if is_attack else ""
        )
        allow_user_attack_requirements = bool(
            scoring_settings.allow_user_attack_requirements
        ) if is_attack else False

    import hashlib
    from selfad.gitea import get_repository_file

    settings = get_gitea_settings()
    try:
        dockerfile = get_repository_file(settings, repository_path, "Dockerfile", ref=batch.commit_sha)
        if dockerfile is None or hashlib.sha256(dockerfile).hexdigest() != expected_dockerfile:
            raise RunnerError("Dockerfile changes are not allowed in participant repositories.")
        if is_attack:
            exploit = get_repository_file(settings, repository_path, "exploit.py", ref=batch.commit_sha)
            if not exploit or not exploit.decode("utf-8", errors="ignore").strip():
                raise RunnerError("Attack repository must contain a non-empty exploit.py.")
            participant_requirements = get_repository_file(
                settings,
                repository_path,
                "requirements.txt",
                ref=batch.commit_sha,
                max_bytes=MAX_REQUIREMENTS_BYTES,
            )
            if (
                participant_requirements
                and participant_requirements.strip()
                and not allow_user_attack_requirements
            ):
                raise RunnerError(
                    "Participant requirements.txt is disabled by the organizer."
                )
            attack_requirements = build_attack_runtime_requirements(
                fixed_attack_requirements,
                (
                    participant_requirements
                    if allow_user_attack_requirements
                    else None
                ),
            )
            contract = ServiceContractResult(True, "Canonical runtime contract.", service.runtime_source_commit, service.runtime_jury_commit, service.container_port, service.healthcheck_path)
            runtime = run_service_runtime_check(settings, repository_path=service.repository_path, jury_repository_path=jury_path, contract=contract, exploit_repository_path=repository_path, exploit_commit=batch.commit_sha, exploit_runtime_requirements=attack_requirements)
        else:
            contract = validate_service_contract(settings, repository_path=repository_path, jury_repository_path=jury_path, default_branch=service.default_branch)
            if not contract.valid:
                raise RunnerError(contract.message)
            runtime = run_service_runtime_check(settings, repository_path=repository_path, jury_repository_path=jury_path, contract=contract)
    except (GiteaError, RunnerError) as error:
        runtime = None
        error_message = str(error)
    else:
        error_message = ""

    with SessionLocal() as session:
        player = session.get(ParticipantService, batch.participant_service_id)
        if player is None:
            _finish_events(session, batch.event_ids, RepositoryEventStatus.DONE, "Participant service no longer exists.")
            session.commit()
            return True
        scoring = get_scoring_settings(session)
        kind = ATTACK if is_attack else DEFENSE
        matched_flags = runtime.matched_flags if runtime is not None else 0
        injected_flags = runtime.injected_flags if runtime is not None else 0
        functionality_passed = bool(
            runtime is not None and runtime.functionality_passed
        )
        completed = bool(runtime is not None and runtime.completed)
        result_message = runtime.message if runtime is not None else error_message
        decision = record_submission_score(
            session,
            player=player,
            settings=scoring,
            kind=kind,
            commit_sha=batch.commit_sha,
            matched_flags=matched_flags,
            injected_flags=injected_flags,
            functionality_passed=functionality_passed,
            completed=completed,
            message=result_message,
        )
        scoring_message = score_message(decision)
        if is_attack:
            if runtime is None:
                player.attack_status = ParticipantRepositoryStatus.FAILED
                player.attack_message = f"{error_message} {scoring_message}"
            else:
                player.attack_status = ParticipantRepositoryStatus.PASSED if runtime.passed else ParticipantRepositoryStatus.FAILED
                player.attack_message = f"{runtime.message} {scoring_message}"
                if runtime.matched_flags:
                    player.defense_unlocked = True
                    player.defense_message = "Defense repository unlocked."
            player.attack_score = max(
                player.attack_score,
                decision.awarded_score,
            )
        else:
            if runtime is None:
                player.defense_status = ParticipantRepositoryStatus.FAILED
                player.defense_message = f"{error_message} {scoring_message}"
            elif not runtime.completed and not runtime.functionality_passed:
                player.defense_status = ParticipantRepositoryStatus.FAILED
                player.defense_message = (
                    f"Functionality violation: {runtime.message} {scoring_message}"
                )
            elif not runtime.completed:
                player.defense_status = ParticipantRepositoryStatus.FAILED
                player.defense_message = f"{runtime.message} {scoring_message}"
            else:
                matched = runtime.matched_flags
                player.defense_status = ParticipantRepositoryStatus.PASSED if matched == 0 else ParticipantRepositoryStatus.FAILED
                player.defense_message = (
                    "Defense check passed: jury exploit recovered no flags."
                    if matched == 0
                    else runtime.message
                )
                player.defense_message = (
                    f"{player.defense_message} {scoring_message}"
                )
            player.defense_score = max(
                player.defense_score,
                decision.awarded_score,
            )
        message = player.attack_message if is_attack else player.defense_message
        newer_push_pending = session.scalar(
            select(RepositoryEvent.id)
            .where(
                RepositoryEvent.status == RepositoryEventStatus.PENDING,
                RepositoryEvent.repository_path == repository_path,
                RepositoryEvent.id.notin_(batch.event_ids),
            )
            .limit(1)
        )
        if newer_push_pending is not None:
            if is_attack:
                player.attack_status = ParticipantRepositoryStatus.RUNNING
                player.attack_message = "A newer attack push is queued for checking."
            else:
                player.defense_status = ParticipantRepositoryStatus.RUNNING
                player.defense_message = "A newer defense push is queued for checking."
        _finish_events(session, batch.event_ids, RepositoryEventStatus.DONE, message)
        session.commit()
    return True


def _finish_events(
    session,
    event_ids: tuple[int, ...],
    status: RepositoryEventStatus,
    message: str,
) -> None:
    session.execute(
        update(RepositoryEvent)
        .where(RepositoryEvent.id.in_(event_ids))
        .values(
            status=status,
            message=message[:2_000],
            processed_at=datetime.now(timezone.utc),
        )
    )
