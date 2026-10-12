import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.application import create_app
from selfad.database import Base, get_session
from selfad.models import BrandingSettings, InstanceConfig, ScoringSettings, User


class SettingsMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details = {}
        self.fields = {}
        self.user_editor = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "user-editor":
            self.user_editor = attrs
        if tag == "details":
            self.details[attrs.get("id")] = "open" in attrs
        if tag in {"input", "select", "textarea"} and attrs.get("name"):
            self.fields.setdefault(attrs["name"], []).append(attrs)


class AdminSettingsLayoutTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            self.admin = User(
                username="admin", email="admin@example.test",
                password_hash="unused", is_admin=True,
            )
            session.add_all([
                self.admin,
                InstanceConfig(id=1, setup_complete=True),
                BrandingSettings(id=1),
                ScoringSettings(
                    id=1, attack_max_points=500, defense_max_points=500,
                    penalty_mode="compound_percent", attack_penalty_value=1,
                    defense_penalty_value=1, attack_requirements="requests==2.32.5",
                ),
            ])
            session.commit()
            session.refresh(self.admin)

        self.app = create_app()

        def test_session():
            with Session(self.engine) as session:
                yield session

        self.app.dependency_overrides[get_session] = test_session
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)
        for target in (
            "selfad.routes.admin_routes.get_admin_access",
            "selfad.routes.admin_settings.get_admin_access",
        ):
            mocked = patch(target, return_value=(self.admin, None))
            mocked.start()
            self.addCleanup(mocked.stop)
        for target, value in (
            ("selfad.routes.admin_shared.runner_is_available", True),
            ("selfad.routes.admin_shared.runner_mode", "external"),
        ):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_user_editor_starts_hidden_above_the_table(self):
        response = self.client.get("/admin?users_page=1")
        self.assertEqual(response.status_code, 200)
        markup = SettingsMarkup(response.text)
        self.assertIn("hidden", markup.user_editor)
        self.assertIn("data-user-add", response.text)
        self.assertLess(response.text.index('id="user-editor"'),
                        response.text.index('class="admin-table user-table"'))

    def form_data(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        markup = SettingsMarkup(response.text)
        return {
            "csrf_token": markup.fields["csrf_token"][0]["value"],
            "contest_state": "not_started",
            "attack_reward_mode": "coverage",
            "attack_max_points": "500",
            "attack_points_per_flag": "10",
            "defense_reward_mode": "coverage",
            "defense_max_points": "500",
            "defense_points_lost_per_flag": "10",
            "penalty_mode": "compound_percent",
            "attack_penalty_value": "1",
            "defense_penalty_value": "1",
            "attack_free_failures": "0",
            "defense_free_failures": "0",
            "stdout_noise_mode": "ignore",
            "stdout_noise_penalty_percent": "1",
            "penalize_check_errors": "on",
            "attack_requirements": "requests==2.32.5",
        }

    def test_optional_settings_start_collapsed_with_enabled_controls(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        markup = SettingsMarkup(response.text)
        self.assertEqual(markup.details, {
            "settings-scoring": False, "settings-penalties": False,
            "settings-runtime": False, "settings-git": False,
        })
        for name in (
            "attack_max_points", "defense_max_points", "penalty_mode",
            "attack_requirements", "allow_user_attack_requirements",
            "gitea_public_enabled",
        ):
            self.assertEqual(len(markup.fields[name]), 1)
            self.assertNotIn("disabled", markup.fields[name][0])
        self.assertEqual(markup.fields["attack_max_points"][0]["value"], "500")

    def test_penalty_error_opens_both_parent_and_nested_module(self):
        data = self.form_data()
        data["attack_penalty_value"] = "-1"
        response = self.client.post("/admin/registration", data=data)
        self.assertEqual(response.status_code, 422)
        markup = SettingsMarkup(response.text)
        self.assertTrue(markup.details["settings-scoring"])
        self.assertTrue(markup.details["settings-penalties"])
        self.assertFalse(markup.details["settings-runtime"])

    def test_public_dependency_error_opens_runtime_module(self):
        data = self.form_data()
        data["allow_user_attack_requirements"] = "on"
        with patch("selfad.routes.admin_settings.public_event_mode_enabled", return_value=True):
            response = self.client.post("/admin/registration", data=data)
        self.assertEqual(response.status_code, 422)
        markup = SettingsMarkup(response.text)
        self.assertTrue(markup.details["settings-runtime"])
        self.assertFalse(markup.details["settings-scoring"])
        self.assertIn("Participant-controlled dependencies are disabled", response.text)

    def test_saving_preserves_values_in_optional_modules(self):
        response = self.client.post(
            "/admin/registration", data=self.form_data(), follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        with Session(self.engine) as session:
            scoring = session.get(ScoringSettings, 1)
            self.assertEqual(scoring.attack_max_points, 500)
            self.assertEqual(scoring.defense_max_points, 500)
            self.assertEqual(scoring.penalty_mode, "compound_percent")
            self.assertEqual(scoring.attack_penalty_value, 1)
            self.assertEqual(scoring.attack_requirements, "requests==2.32.5")


if __name__ == "__main__":
    unittest.main()
