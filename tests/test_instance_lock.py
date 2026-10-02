import os
import tempfile
import unittest
from unittest.mock import patch

from selfad.database import application_instance_lock


class InstanceLockTests(unittest.TestCase):
    def test_second_control_plane_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = os.path.join(directory, "instance.lock")
            with patch.dict(
                os.environ,
                {"SELFAD_INSTANCE_LOCK_FILE": lock_path},
            ):
                with application_instance_lock():
                    with self.assertRaisesRegex(RuntimeError, "Another SelfAD"):
                        with application_instance_lock():
                            self.fail("the second lock must not be acquired")


if __name__ == "__main__":
    unittest.main()
