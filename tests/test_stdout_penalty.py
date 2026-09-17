import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from selfad.database import Base
from selfad.models import ParticipantService, ScoringSettings
from selfad.runner import _run_command, _stdout_has_noise
from selfad.scoring import ATTACK, record_submission_score


FLAG = "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6"


class StdoutPenaltyTests(unittest.TestCase):
    def test_only_non_flag_stdout_is_noise(self):
        self.assertFalse(_stdout_has_noise(f"{FLAG}\n"))
        self.assertTrue(_stdout_has_noise(f"debug\n{FLAG}\n"))
        self.assertTrue(_stdout_has_noise("\n"))

    def test_command_result_keeps_stdout_and_stderr_separate(self):
        result = _run_command(
            ["/bin/sh", "-c", "printf output; printf error >&2"],
            timeout=2,
            max_output=1024,
            environment={},
        )
        self.assertEqual(result.stdout, "output")
        self.assertEqual(result.stderr, "error")

    def test_noisy_attack_adds_debt_and_awards_no_score(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            settings = ScoringSettings(
                id=1,
                attack_max_points=100,
                penalty_mode="points",
                attack_penalty_value=10,
                penalize_check_errors=False,
                stdout_noise_mode="unsuccessful",
            )
            player = ParticipantService(
                service_id=1,
                user_id=1,
                attack_repository_path="root/test-attack",
                defense_repository_path="root/test-defense",
                attack_dockerfile_sha="a" * 64,
                defense_dockerfile_sha="b" * 64,
            )
            session.add_all([settings, player])
            session.flush()

            noisy = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="c" * 40,
                matched_flags=1,
                injected_flags=1,
                functionality_passed=True,
                completed=True,
                stdout_noise=True,
                message="noisy",
            )
            session.flush()
            clean = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="d" * 40,
                matched_flags=1,
                injected_flags=1,
                functionality_passed=True,
                completed=True,
                stdout_noise=False,
                message="clean",
            )

        self.assertEqual(noisy.awarded_score, 0)
        self.assertEqual(noisy.unsuccessful_attempts, 1)
        self.assertEqual(clean.raw_score, 100)
        self.assertEqual(clean.penalty, 10)
        self.assertEqual(clean.awarded_score, 90)

    def test_noisy_attack_can_add_a_percentage_cost_without_rejection(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            settings = ScoringSettings(
                id=1,
                attack_max_points=100,
                penalty_mode="percent",
                attack_penalty_value=5,
                stdout_noise_mode="percent_penalty",
                stdout_noise_penalty_percent=1,
            )
            player = ParticipantService(
                service_id=1,
                user_id=1,
                attack_repository_path="root/test-attack",
                defense_repository_path="root/test-defense",
                attack_dockerfile_sha="a" * 64,
                defense_dockerfile_sha="b" * 64,
            )
            session.add_all([settings, player])
            session.flush()

            first_failure = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="a" * 40,
                matched_flags=0,
                injected_flags=1,
                functionality_passed=True,
                completed=True,
                message="no flags",
            )
            noisy = record_submission_score(
                session,
                player=player,
                settings=settings,
                kind=ATTACK,
                commit_sha="b" * 40,
                matched_flags=1,
                injected_flags=1,
                functionality_passed=True,
                completed=True,
                stdout_noise=True,
                message="flag plus debug",
            )

        self.assertEqual(first_failure.unsuccessful_attempts, 1)
        # 5% debt from the failed attempt plus the 1% noisy-output cost.
        self.assertEqual(noisy.penalty, 6)
        self.assertEqual(noisy.awarded_score, 94)


if __name__ == "__main__":
    unittest.main()
