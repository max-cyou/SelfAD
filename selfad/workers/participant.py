from sqlalchemy import select

from selfad.contest import STARTED, contest_state, start_contest_if_due
from selfad.database import SessionLocal
from selfad.gitea import GiteaError, GiteaUnavailable
from selfad.models import (
    InstanceConfig,
    ParticipantRepositoryStatus,
    ParticipantService,
    RepositoryEvent,
    RepositoryEventStatus,
    Service,
)
from selfad.runner import (
    MAX_REQUIREMENTS_BYTES,
    RunnerError,
    build_attack_runtime_requirements,
    run_service_runtime_check,
)
from selfad.runtime.types import RunnerInfrastructureError
from selfad.scoring import (
    ATTACK,
    DEFENSE,
    get_scoring_settings,
    record_submission_score,
    score_message,
)
from selfad.service_contract import ServiceContractResult, validate_service_contract
from selfad.settings import get_gitea_settings
from selfad.workers.queue import (
    _batch_is_superseded,
    _finish_events,
)
from selfad.workers.recovery import lock_batch
from selfad.workers.types import (
    RepositoryBatch,
)


def _process_participant_batch(batch: RepositoryBatch) -> bool:
    with SessionLocal() as session:
        if not lock_batch(session, batch):
            return True
        player = session.get(ParticipantService, batch.participant_service_id)
        service = session.get(Service, batch.service_id)
        if player is None or service is None or not batch.repository_path or not batch.commit_sha:
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.FAILED,
                "Participant repository is unavailable.",
            )
            session.commit()
            return True
        if _batch_is_superseded(session, batch):
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                "Superseded by a newer push.",
            )
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
        expected_dockerfile = (
            player.attack_dockerfile_sha if is_attack else player.defense_dockerfile_sha
        )
        repository_path = batch.repository_path
        jury_path = service.jury_repository_path
        if not jury_path:
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.FAILED,
                "Jury repository is unavailable.",
            )
            session.commit()
            return True
        if not is_attack and not player.defense_unlocked:
            player.defense_status = ParticipantRepositoryStatus.FAILED
            player.defense_message = "Defense unlocks after a successful attack."
            _finish_events(
                session, batch.event_ids, RepositoryEventStatus.FAILED, player.defense_message
            )
            session.commit()
            return True
        fixed_attack_requirements = scoring_settings.attack_requirements if is_attack else ""
        allow_user_attack_requirements = (
            bool(scoring_settings.allow_user_attack_requirements) if is_attack else False
        )

    import hashlib

    from selfad.gitea import get_repository_file

    settings = get_gitea_settings()
    try:
        dockerfile = get_repository_file(
            settings, repository_path, "Dockerfile", ref=batch.commit_sha
        )
        if dockerfile is None or hashlib.sha256(dockerfile).hexdigest() != expected_dockerfile:
            raise RunnerError("Dockerfile changes are not allowed in participant repositories.")
        if is_attack:
            exploit = get_repository_file(
                settings, repository_path, "exploit.py", ref=batch.commit_sha
            )
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
                raise RunnerError("Participant requirements.txt is disabled by the organizer.")
            attack_requirements = build_attack_runtime_requirements(
                fixed_attack_requirements,
                (participant_requirements if allow_user_attack_requirements else None),
            )
            contract = ServiceContractResult(
                True,
                "Canonical runtime contract.",
                service.runtime_source_commit,
                service.runtime_jury_commit,
                service.container_port,
                service.healthcheck_path,
            )
            runtime = run_service_runtime_check(
                settings,
                repository_path=service.repository_path,
                jury_repository_path=jury_path,
                contract=contract,
                exploit_repository_path=repository_path,
                exploit_commit=batch.commit_sha,
                exploit_runtime_requirements=attack_requirements,
                cache_service_image=True,
            )
        else:
            jury_commit = service.runtime_jury_commit or service.validated_jury_commit
            if not jury_commit:
                raise RunnerError(
                    "The canonical jury commit is unavailable; validate the service first."
                )
            contract = validate_service_contract(
                settings,
                repository_path=repository_path,
                jury_repository_path=jury_path,
                default_branch=service.default_branch,
                source_commit=batch.commit_sha,
                jury_commit=jury_commit,
            )
            if not contract.valid:
                raise RunnerError(contract.message)
            runtime = run_service_runtime_check(
                settings,
                repository_path=repository_path,
                jury_repository_path=jury_path,
                contract=contract,
            )
    except (GiteaUnavailable, RunnerInfrastructureError):
        raise
    except (GiteaError, RunnerError) as error:
        runtime = None
        error_message = str(error)
    else:
        error_message = ""

    with SessionLocal() as session:
        # Claim the durable result fence before locking player counters. This
        # transaction also stores the attempt, best score and DONE event state.
        if not lock_batch(session, batch, scoring=True):
            return True
        # Serialize the final result with webhook status updates. Without the
        # row lock, a push can be inserted after the superseded check and its
        # RUNNING state can then be overwritten by this older batch.
        player = session.scalar(
            select(ParticipantService)
            .where(ParticipantService.id == batch.participant_service_id)
            .with_for_update()
        )
        if player is None:
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                "Participant service no longer exists.",
            )
            session.commit()
            return True
        if _batch_is_superseded(session, batch):
            _finish_events(
                session,
                batch.event_ids,
                RepositoryEventStatus.DONE,
                "Superseded by a newer push.",
            )
            session.commit()
            return True
        scoring = get_scoring_settings(session)
        kind = ATTACK if is_attack else DEFENSE
        matched_flags = runtime.matched_flags if runtime is not None else 0
        injected_flags = runtime.injected_flags if runtime is not None else 0
        functionality_passed = bool(runtime is not None and runtime.functionality_passed)
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
            stdout_noise=bool(is_attack and runtime and runtime.stdout_noise),
            message=result_message,
        )
        scoring_message = score_message(decision)
        if is_attack:
            noisy_stdout_rejected = bool(
                runtime is not None
                and runtime.stdout_noise
                and scoring.stdout_noise_mode == "unsuccessful"
            )
            if runtime is None:
                player.attack_status = ParticipantRepositoryStatus.FAILED
                player.attack_message = f"{error_message} {scoring_message}"
            elif noisy_stdout_rejected:
                player.attack_status = ParticipantRepositoryStatus.FAILED
                player.attack_message = f"Attack stdout contains non-flag output. {scoring_message}"
            else:
                player.attack_status = (
                    ParticipantRepositoryStatus.PASSED
                    if runtime.passed
                    else ParticipantRepositoryStatus.FAILED
                )
                player.attack_message = f"{runtime.message} {scoring_message}"
                if runtime.matched_flags and not noisy_stdout_rejected:
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
                player.defense_status = (
                    ParticipantRepositoryStatus.PASSED
                    if matched == 0
                    else ParticipantRepositoryStatus.FAILED
                )
                player.defense_message = (
                    "Defense check passed: jury exploit recovered no flags."
                    if matched == 0
                    else (
                        "Defense check failed: the jury exploit recovered "
                        f"{matched} of {runtime.injected_flags} flags."
                    )
                )
                player.defense_message = f"{player.defense_message} {scoring_message}"
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
