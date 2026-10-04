import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from selfad.database import Base
from selfad.models import ParticipantService, ScoringSettings, SubmissionAttempt
from selfad.scoring import (
    ATTACK,
    MAX_ATTEMPT_HISTORY_PER_SERVICE,
    record_submission_score,
)


def _make_player() -> ParticipantService:
    return ParticipantService(
        service_id=1,
        user_id=1,
        attack_repository_path="root/test-attack",
        defense_repository_path="root/test-defense",
        attack_dockerfile_sha="a" * 64,
        defense_dockerfile_sha="b" * 64,
    )


class AttemptAggregatesTests(unittest.TestCase):
    def test_penalized_raw_improvement_does_not_move_score_timestamp(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        reached_at = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
        with Session(engine) as session:
            settings = ScoringSettings(
                id=1,
                attack_max_points=100,
                penalty_mode="percent",
                attack_penalty_value=50,
            )
            player = _make_player()
            player.attack_score = 60
            player.attack_best_raw = 60
            player.attack_penalty_attempts = 1
            player.first_awarded_at = reached_at
            player.last_awarded_at = reached_at
            session.add_all([settings, player])
            session.flush()

            decision = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="e" * 40,
                matched_flags=7,
                injected_flags=10,
                functionality_passed=True,
                completed=True,
                message="ok",
            )

            self.assertTrue(decision.improved)
            self.assertEqual(decision.raw_score, 70)
            self.assertEqual(decision.awarded_score, 35)
            self.assertEqual(player.first_awarded_at, reached_at)
            self.assertEqual(player.last_awarded_at, reached_at)

    def test_counters_and_pruning_keep_scoring_stable(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            settings = ScoringSettings(
                id=1,
                attack_max_points=100,
                penalty_mode="none",
                stdout_noise_mode="ignore",
            )
            player = _make_player()
            session.add_all([settings, player])
            session.flush()

            for _ in range(MAX_ATTEMPT_HISTORY_PER_SERVICE + 30):
                record_submission_score(
                    session,
                    player=player,
                    settings=settings,
                    kind=ATTACK,
                    commit_sha="c" * 40,
                    matched_flags=1,
                    injected_flags=1,
                    functionality_passed=True,
                    completed=True,
                    message="ok",
                )
            session.flush()

            stored = session.scalar(
                select(func.count()).select_from(SubmissionAttempt).where(
                    SubmissionAttempt.participant_service_id == player.id
                )
            )
            self.assertEqual(stored, MAX_ATTEMPT_HISTORY_PER_SERVICE)
            self.assertEqual(player.attack_attempt_count, 80)
            self.assertEqual(player.attack_best_raw, 100)
            first_awarded_at = player.first_awarded_at
            last_awarded_at = player.last_awarded_at

            decision = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="d" * 40,
                matched_flags=1,
                injected_flags=1,
                functionality_passed=True,
                completed=True,
                message="ok",
            )
            session.flush()
            self.assertFalse(decision.improved)
            latest = session.scalars(
                select(SubmissionAttempt)
                .where(SubmissionAttempt.participant_service_id == player.id)
                .order_by(SubmissionAttempt.id.desc())
                .limit(1)
            ).first()
            self.assertEqual(latest.attempt_number, 81)
            self.assertIsNotNone(player.first_awarded_at)
            self.assertIsNotNone(player.last_awarded_at)
            self.assertEqual(player.first_awarded_at, first_awarded_at)
            self.assertEqual(player.last_awarded_at, last_awarded_at)


if __name__ == "__main__":
    unittest.main()
