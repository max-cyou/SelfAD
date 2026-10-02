import os
import unittest
from unittest.mock import patch

from selfad.routes.admin_settings import (
    add_public_scoring_errors,
    parse_scoring_form,
)
from selfad.settings import public_event_mode_enabled


class PublicScoringSettingsTests(unittest.TestCase):
    def test_public_mode_flag_is_detected(self):
        with patch.dict(
            os.environ,
            {"SELFAD_PUBLIC_EVENT_MODE": "true"},
        ):
            self.assertTrue(public_event_mode_enabled())

    def test_requirements_form_exposes_participant_dependency_choice(self):
        values, errors = parse_scoring_form(
            {
                "attack_reward_mode": "coverage",
                "defense_reward_mode": "per_flag",
                "penalty_mode": "none",
                "stdout_noise_mode": "ignore",
                "attack_max_points": "100",
                "attack_points_per_flag": "10",
                "defense_max_points": "100",
                "defense_points_lost_per_flag": "10",
                "attack_free_failures": "0",
                "defense_free_failures": "0",
                "attack_penalty_value": "0",
                "defense_penalty_value": "0",
                "stdout_noise_penalty_percent": "0",
                "allow_user_attack_requirements": "on",
            }
        )
        self.assertFalse(errors)
        self.assertTrue(values["allow_user_attack_requirements"])
        with patch.dict(
            os.environ,
            {"SELFAD_PUBLIC_EVENT_MODE": "true"},
        ):
            add_public_scoring_errors(values, errors)
        self.assertIn("allow_user_attack_requirements", errors)


if __name__ == "__main__":
    unittest.main()
