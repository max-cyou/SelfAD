from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from selfad.models import ParticipantService, ScoringSettings, SubmissionAttempt


ATTACK = "attack"
DEFENSE = "defense"
REWARD_MODES = {"coverage", "per_flag"}
PENALTY_MODES = {"none", "points", "percent", "compound_percent"}
STDOUT_NOISE_MODES = {"ignore", "unsuccessful", "percent_penalty"}
# SubmissionAttempt rows are an audit tail; scoring and the scoreboard read
# the denormalised counters on ParticipantService, so the tail can be pruned.
MAX_ATTEMPT_HISTORY_PER_SERVICE = 50


@dataclass(frozen=True)
class ScoreDecision:
    raw_score: int
    awarded_score: int
    penalty: int
    improved: bool
    unsuccessful_attempts: int
    penalty_attempts: int


def get_scoring_settings(session: Session) -> ScoringSettings:
    settings = session.get(ScoringSettings, 1)
    if settings is None:
        settings = ScoringSettings(id=1)
        session.add(settings)
        session.flush()
    return settings


def calculate_raw_score(
    settings: ScoringSettings,
    *,
    kind: str,
    matched_flags: int,
    injected_flags: int,
    completed: bool,
) -> int:
    if not completed or injected_flags <= 0:
        return 0

    matched = max(0, min(matched_flags, injected_flags))
    if kind == ATTACK:
        maximum = max(0, settings.attack_max_points)
        if settings.attack_reward_mode == "per_flag":
            return min(maximum, matched * max(0, settings.attack_points_per_flag))
        return _round_points(maximum * matched / injected_flags)

    maximum = max(0, settings.defense_max_points)
    if settings.defense_reward_mode == "coverage":
        return _round_points(
            maximum * (injected_flags - matched) / injected_flags
        )
    return max(
        0,
        maximum - matched * max(0, settings.defense_points_lost_per_flag),
    )


def record_submission_score(
    session: Session,
    *,
    player: ParticipantService,
    settings: ScoringSettings,
    kind: str,
    commit_sha: str,
    matched_flags: int,
    injected_flags: int,
    functionality_passed: bool,
    completed: bool,
    message: str,
    stdout_noise: bool = False,
) -> ScoreDecision:
    current_score = player.attack_score if kind == ATTACK else player.defense_score
    if kind == ATTACK:
        previous_attempts = player.attack_attempt_count
        previous_best_raw = max(player.attack_best_raw, current_score)
        previous_penalty_attempts = player.attack_penalty_attempts
    else:
        previous_attempts = player.defense_attempt_count
        previous_best_raw = max(player.defense_best_raw, current_score)
        previous_penalty_attempts = player.defense_penalty_attempts

    stdout_noise_mode = getattr(settings, "stdout_noise_mode", "ignore")
    # Compatibility for databases created before the mode selector existed.
    if stdout_noise_mode not in STDOUT_NOISE_MODES:
        stdout_noise_mode = "ignore"
    if (
        stdout_noise_mode == "ignore"
        and getattr(settings, "penalize_stdout_noise", False)
    ):
        stdout_noise_mode = "unsuccessful"
    noisy_attack_output = (
        kind == ATTACK
        and stdout_noise
        and stdout_noise_mode == "unsuccessful"
    )
    raw_score = calculate_raw_score(
        settings,
        kind=kind,
        matched_flags=matched_flags,
        injected_flags=injected_flags,
        completed=completed and not noisy_attack_output,
    )
    improved = raw_score > previous_best_raw
    check_error = not completed
    penalty_eligible = (
        not improved
        and settings.penalty_mode != "none"
        and (
            noisy_attack_output
            or settings.penalize_check_errors
            or not check_error
        )
    )
    free_failures = (
        settings.attack_free_failures
        if kind == ATTACK
        else settings.defense_free_failures
    )
    penalty_attempts = max(0, previous_penalty_attempts - free_failures)
    penalty_value = (
        settings.attack_penalty_value
        if kind == ATTACK
        else settings.defense_penalty_value
    )
    penalty = _calculate_penalty(
        settings.penalty_mode,
        raw_score,
        penalty_attempts,
        penalty_value,
    ) if improved else 0
    if (
        improved
        and kind == ATTACK
        and stdout_noise
        and stdout_noise_mode == "percent_penalty"
    ):
        noise_penalty = _round_points(
            raw_score * min(
                100.0,
                max(0.0, settings.stdout_noise_penalty_percent),
            ) / 100
        )
        # The output fee is additive: accumulated unsuccessful-attempt debt is
        # still applied to the same result.
        penalty = min(raw_score, penalty + noise_penalty)
    awarded_score = max(0, raw_score - penalty) if improved else 0

    session.add(
        SubmissionAttempt(
            participant_service_id=player.id,
            repository_path=(
                player.attack_repository_path
                if kind == ATTACK
                else player.defense_repository_path
            ),
            kind=kind,
            commit_sha=commit_sha,
            attempt_number=previous_attempts + 1,
            matched_flags=max(0, matched_flags),
            injected_flags=max(0, injected_flags),
            functionality_passed=functionality_passed,
            completed=completed,
            improved=improved,
            penalty_eligible=penalty_eligible,
            stdout_noise=stdout_noise,
            raw_score=raw_score,
            penalty=penalty,
            awarded_score=awarded_score,
            message=message[:2_000],
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    if kind == ATTACK:
        player.attack_attempt_count = previous_attempts + 1
        player.attack_best_raw = max(previous_best_raw, raw_score)
        player.attack_penalty_attempts = (
            previous_penalty_attempts + int(penalty_eligible)
        )
    else:
        player.defense_attempt_count = previous_attempts + 1
        player.defense_best_raw = max(previous_best_raw, raw_score)
        player.defense_penalty_attempts = (
            previous_penalty_attempts + int(penalty_eligible)
        )
    if awarded_score > 0:
        now = datetime.now(timezone.utc)
        if player.first_awarded_at is None:
            player.first_awarded_at = now
        player.last_awarded_at = now
    kept_attempt_ids = session.scalars(
        select(SubmissionAttempt.id)
        .where(SubmissionAttempt.participant_service_id == player.id)
        .order_by(SubmissionAttempt.id.desc())
        .limit(MAX_ATTEMPT_HISTORY_PER_SERVICE)
    ).all()
    if kept_attempt_ids:
        session.execute(
            delete(SubmissionAttempt).where(
                SubmissionAttempt.participant_service_id == player.id,
                SubmissionAttempt.id.not_in(kept_attempt_ids),
            )
        )
    return ScoreDecision(
        raw_score=raw_score,
        awarded_score=awarded_score,
        penalty=penalty,
        improved=improved,
        unsuccessful_attempts=previous_penalty_attempts + int(penalty_eligible),
        penalty_attempts=penalty_attempts,
    )


def score_message(decision: ScoreDecision) -> str:
    if decision.improved:
        return (
            f"Score: {decision.raw_score} raw - {decision.penalty} penalty "
            f"= {decision.awarded_score} pts."
        )
    if decision.unsuccessful_attempts:
        return (
            "No score improvement; unsuccessful submission recorded "
            f"({decision.unsuccessful_attempts} total)."
        )
    return "No score improvement."


def _calculate_penalty(
    mode: str,
    raw_score: int,
    attempts: int,
    value: float,
) -> int:
    if mode == "points":
        return min(raw_score, _round_points(max(0.0, value) * attempts))
    if mode == "percent":
        percentage = min(100.0, max(0.0, value) * attempts)
        return min(raw_score, _round_points(raw_score * percentage / 100))
    if mode == "compound_percent":
        rate = min(100.0, max(0.0, value)) / 100
        percentage = 1 - (1 - rate) ** attempts
        return min(raw_score, _round_points(raw_score * percentage))
    return 0


def _round_points(value: float) -> int:
    return int(max(0.0, value) + 0.5)
