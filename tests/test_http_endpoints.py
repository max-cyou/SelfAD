import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from selfad.application import create_app
from selfad.database import Base, get_session
from selfad.models import RepositoryEvent

PROJECT_DIR = Path(__file__).resolve().parents[1]


class HttpEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.app = create_app()

        def test_session():
            with Session(self.engine) as session:
                yield session

        self.app.dependency_overrides[get_session] = test_session
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def test_health_has_security_headers(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])

    def test_admin_redirects_to_setup_before_configuration(self):
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")

    def test_public_setup_requires_token_then_uses_signed_session(self):
        token = "u" * 48
        with patch.dict(
            os.environ,
            {
                "SELFAD_PUBLIC_EVENT_MODE": "true",
                "SELFAD_SETUP_TOKEN": token,
            },
        ):
            denied = self.client.get("/setup")
            self.assertEqual(denied.status_code, 404)
            authorized = self.client.get(
                f"/setup?setup_token={token}",
                follow_redirects=False,
            )
            self.assertEqual(authorized.status_code, 303)
            self.assertEqual(authorized.headers["location"], "/setup")
            form = self.client.get("/setup")
            self.assertEqual(form.status_code, 200)

    def test_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            "/hooks/gitea",
            content=b"{}",
            headers={"x-gitea-signature": "invalid"},
        )
        self.assertEqual(response.status_code, 401)

    def test_metrics_expose_queue_age_with_authentication(self):
        with Session(self.engine) as session:
            session.add(
                RepositoryEvent(
                    delivery_id="metrics-delivery",
                    repository_path="root/metrics",
                    ref="refs/heads/main",
                    commit_sha="b" * 40,
                )
            )
            session.commit()
        with patch.dict(os.environ, {"SELFAD_METRICS_TOKEN": "m" * 48}):
            response = self.client.get(
                "/metrics",
                headers={"Authorization": f"Bearer {'m' * 48}"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'selfad_oldest_repository_event_seconds{status="pending"}',
            response.text,
        )

    def test_signed_unknown_repository_is_safely_ignored(self):
        secret = "w" * 48
        body = json.dumps(
            {
                "repository": {"full_name": "root/unknown"},
                "ref": "refs/heads/main",
                "after": "a" * 40,
            }
        ).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with patch(
            "selfad.routes.webhooks.get_gitea_webhook_secret",
            return_value=secret,
        ):
            response = self.client.post(
                "/hooks/gitea",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-gitea-event": "push",
                    "x-gitea-delivery": "http-test-delivery",
                    "x-gitea-signature": signature,
                },
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"message": "Repository ignored."})

    def test_full_lifespan_starts_on_a_fresh_database(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment.update(
                SELFAD_DATA_DIR=directory,
                SELFAD_DATABASE_URL=f"sqlite:///{Path(directory) / 'app.db'}",
                SELFAD_SECRET_KEY="s" * 48,
                SELFAD_RUNNER_INSTANCE_ID="http-lifespan-test",
            )
            script = """
from unittest.mock import patch
from fastapi.testclient import TestClient
from selfad.application import create_app
with (
    patch('selfad.application.cleanup_managed_runner_resources', return_value=0),
    patch('selfad.application.reconcile_administrator_gitea_identity'),
    patch('selfad.application.reconcile_repository_webhooks'),
):
    with TestClient(create_app()) as client:
        assert client.get('/health').status_code == 200
"""
            subprocess.run(
                [sys.executable, "-c", script],
                cwd=PROJECT_DIR,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
