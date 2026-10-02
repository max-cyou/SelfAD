import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from selfad.application import validate_public_event_database_policy
from selfad.database import Base
from selfad.models import ScoringSettings


class PublicDatabasePolicyTests(unittest.TestCase):
    def test_public_mode_rejects_participant_dependencies(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        local_session = sessionmaker(bind=engine)
        with Session(engine) as session:
            session.add(
                ScoringSettings(
                    id=1,
                    allow_user_attack_requirements=True,
                )
            )
            session.commit()
        with (
            patch.dict(
                os.environ,
                {"SELFAD_PUBLIC_EVENT_MODE": "true"},
            ),
            patch("selfad.application.SessionLocal", local_session),
        ):
            with self.assertRaisesRegex(RuntimeError, "participant requirements"):
                validate_public_event_database_policy()
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
