"""Run the same fencing/concurrency cases in a disposable PostgreSQL schema."""

import os
import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from tests import test_submission_admission as admission_tests
from tests import test_worker_recovery as recovery_tests

DATABASE_URL = os.getenv("SELFAD_TEST_POSTGRES_URL", "")


@unittest.skipUnless(DATABASE_URL.startswith("postgresql"), "PostgreSQL recovery tests not configured")
class PostgreSQLWorkerRecoveryTests(recovery_tests.WorkerRecoveryTests):
    def make_engine(self, directory):
        schema = "selfad_recovery_" + uuid.uuid4().hex
        control = create_engine(DATABASE_URL)
        with control.begin() as connection:
            connection.execute(CreateSchema(schema))

        def cleanup():
            try:
                with control.begin() as connection:
                    connection.execute(DropSchema(schema, cascade=True))
            finally:
                control.dispose()

        self.addCleanup(cleanup)
        return create_engine(DATABASE_URL, execution_options={"schema_translate_map": {None: schema}})


@unittest.skipUnless(DATABASE_URL.startswith("postgresql"), "PostgreSQL admission tests not configured")
class PostgreSQLSubmissionAdmissionTests(admission_tests.SubmissionAdmissionTests):
    fixture_class = PostgreSQLWorkerRecoveryTests
