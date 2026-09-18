import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from selfad.runner import (
    SERVICE_CACHE_LABEL,
    _prepare_runtime_image,
    _service_cache_image_name,
)


class RunnerCacheTests(unittest.TestCase):
    def test_service_cache_name_is_stable_for_a_source_commit(self):
        commit_sha = "a" * 40
        self.assertEqual(
            _service_cache_image_name(commit_sha),
            f"selfad-service-cache-{commit_sha}:latest",
        )
        self.assertNotEqual(SERVICE_CACHE_LABEL, "selfad.managed=true")

    def test_generated_runtime_uses_content_addressed_pip_cache(self):
        requirements = b"requests==2.32.5\n"
        namespace = "attack:root/test"
        expected_id = hashlib.sha256(
            namespace.encode() + b"\0" + requirements
        ).hexdigest()[:16]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "exploit.py").write_text("print('ok')\n")
            context = root / "context"
            with patch("selfad.runner._docker") as docker:
                docker.return_value.returncode = 0
                _prepare_runtime_image(
                    requirements,
                    "test-image:latest",
                    context,
                    scripts,
                    cache_namespace=namespace,
                )
            dockerfile = (context / "Dockerfile").read_text()
        self.assertIn(f"id=selfad-pip-{expected_id}", dockerfile)
        self.assertIn("target=/root/.cache/pip", dockerfile)
        self.assertNotIn("--no-cache-dir", dockerfile)


if __name__ == "__main__":
    unittest.main()
