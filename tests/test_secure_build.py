import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from selfad.runtime import build, security_check
from selfad.runtime.requirements import _prepare_runtime_image
from selfad.runtime.types import CommandResult, RunnerError, RunnerInfrastructureError


class SecureBuilderTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {
            "Driver": "docker-container",
            "Nodes": [{"Name": "selfad-secure0", "Endpoint": "selfad-secure-runner"}],
        }
        self.context = [
            {"Endpoints": {"docker": {"Host": "tcp://runner:2376", "SkipTLSVerify": False}}}
        ]
        self.container = [
            {
                "State": {"Running": True},
                "Args": ["--oci-worker-snapshotter=native", "--oci-worker-net=bridge"],
                "Config": {"Cmd": None},
                "HostConfig": {
                    "Memory": build.BUILD_MEMORY,
                    "MemorySwap": build.BUILD_MEMORY,
                    "CpuPeriod": 100000,
                    "CpuQuota": 200000,
                    "PidsLimit": 512,
                    "NetworkMode": "bridge",
                    "CgroupnsMode": "private",
                },
                "Mounts": [{"Type": "volume", "Destination": "/var/lib/buildkit", "Name": "state"}],
            }
        ]
        self.volume = [
            {
                "Driver": "local",
                "Options": {"type": "tmpfs", "device": "tmpfs", "o": f"size={build.BUILD_MEMORY}"},
            }
        ]
        config = patch.object(
            build,
            "get_runner_settings",
            return_value=SimpleNamespace(docker_host="tcp://runner:2376"),
        )
        config.start()
        self.addCleanup(config.stop)
        env = patch.dict(os.environ, {"SELFAD_RUNNER_BUILDER": "selfad-secure"})
        env.start()
        self.addCleanup(env.stop)

    def verify(self):
        with (
            patch.object(
                build,
                "_docker",
                return_value=CommandResult(0, f"{build.BUILD_MEMORY}\n0\n512\n200000 100000\n"),
            ),
            patch.object(
                build,
                "_json",
                side_effect=[self.metadata, self.context, self.container, self.volume],
            ),
        ):
            return build.verify_builder()

    def test_bounded_builder_is_accepted(self):
        self.verify()

    def test_unsafe_runtime_limits_fail_closed(self):
        original = copy.deepcopy(self.container)
        for key, value in [
            ("Memory", 0),
            ("MemorySwap", -1),
            ("PidsLimit", 0),
            ("CpuQuota", -1),
            ("NetworkMode", "host"),
            ("PidMode", "host"),
        ]:
            with self.subTest(key=key):
                self.container = copy.deepcopy(original)
                self.container[0]["HostConfig"][key] = value
                with self.assertRaises(RunnerError):
                    self.verify()

    def test_disk_backed_state_and_extra_mounts_are_rejected(self):
        self.volume[0]["Options"]["type"] = "ext4"
        with self.assertRaises(RunnerError):
            self.verify()
        self.volume[0]["Options"]["type"] = "tmpfs"
        self.container[0]["Mounts"].append({"Type": "bind", "Destination": "/certs"})
        with self.assertRaises(RunnerError):
            self.verify()

    def test_insecure_entitlements_and_wrong_endpoints_are_rejected(self):
        self.container[0]["Args"].append("--allow-insecure-entitlement=network.host")
        with self.assertRaises(RunnerError):
            self.verify()
        self.container[0]["Args"] = []
        self.context[0]["Endpoints"]["docker"]["Host"] = "tcp://wrong-runner:2376"
        with self.assertRaises(RunnerError):
            self.verify()

    def test_malformed_inspect_is_not_a_readiness_success(self):
        with patch.object(build, "_json", return_value={}):
            with self.assertRaises(RunnerError):
                build.verify_builder()

    def test_missing_build_network_firewall_fails_closed(self):
        with (
            patch.object(build, "_json", side_effect=[self.metadata, self.context, self.container]),
            patch.object(build, "_docker", return_value=CommandResult(1, "missing firewall")),
        ):
            with self.assertRaisesRegex(RunnerError, "network firewall"):
                build.verify_builder()

    def test_initialize_refuses_wrong_context_before_mutating_runner(self):
        self.context[0]["Endpoints"]["docker"]["Host"] = "tcp://wrong:2376"
        with (
            patch.object(build, "_json", return_value=self.context),
            patch.object(build, "_docker", return_value=CommandResult(0, "exists")) as docker,
        ):
            with self.assertRaisesRegex(RunnerError, "different or insecure runner"):
                build.initialize_builder()
        self.assertEqual(len(docker.call_args_list), 1)

    def test_kernel_limits_must_match_not_just_hostconfig(self):
        with (
            patch.object(
                build,
                "_json",
                side_effect=[
                    self.metadata,
                    self.context,
                    self.container,
                    self.volume,
                ],
            ),
            patch.object(
                build,
                "_docker",
                side_effect=[
                    CommandResult(0, "firewall ok"),
                    CommandResult(0, "max\nmax\nmax\nmax 100000\n"),
                ],
            ),
        ):
            with self.assertRaisesRegex(RunnerError, "Kernel cgroup limits"):
                build.verify_builder()

    def test_existing_builder_is_verified_before_firewall_exec(self):
        with (
            patch.object(build, "_json", return_value=self.context),
            patch.object(build, "_verify_builder", side_effect=RunnerError("unsafe namespace")),
            patch.object(build, "_docker", return_value=CommandResult(0, "exists")) as docker,
        ):
            with self.assertRaisesRegex(RunnerError, "unsafe namespace"):
                build.initialize_builder()
        self.assertFalse(any(call.args[0][0] == "exec" for call in docker.call_args_list))

    def test_public_mode_never_falls_back_to_plain_build(self):
        with (
            patch.object(build, "public_event_mode_enabled", return_value=True),
            patch.object(build, "verify_builder", side_effect=RunnerError("unsafe")),
            patch.object(build, "_docker") as docker,
        ):
            with self.assertRaises(RunnerInfrastructureError):
                build.build_image(Path("/tmp"), "test:latest", [])
        docker.assert_not_called()

    def test_secure_build_has_no_network_and_a_hard_client_file_limit(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(build, "WORK_DIR", Path(folder)),
            patch.object(build, "verify_builder"),
            patch.object(build, "check_image_budget"),
            patch.object(build, "validate_image_archive"),
            patch.object(build, "_docker", return_value=CommandResult(0, "ok")) as docker,
        ):
            build.build_image(Path(folder), "test:latest", [])
        args = docker.call_args_list[0].args[0]
        self.assertEqual(args[:2], ["buildx", "build"])
        self.assertIn("--network=none", args)
        self.assertNotIn("--load", args)
        self.assertEqual(docker.call_args_list[0].kwargs["file_limit"], build.MAX_IMAGE_ARCHIVE)
        self.assertEqual(docker.call_args_list[1].args[0][0], "load")

    def test_image_storage_budget_blocks_new_builds(self):
        with patch.object(
            build,
            "_docker",
            side_effect=[
                CommandResult(0, "sha256:" + "a" * 64),
                CommandResult(0, str(build.MAX_RUNNER_IMAGES)),
            ],
        ):
            with self.assertRaises(RunnerInfrastructureError):
                build.check_image_budget()

    def test_public_dependencies_require_prepared_image(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("selfad.runtime.requirements._docker", return_value=CommandResult(0, "ok")),
            patch("selfad.runtime.requirements.public_event_mode_enabled", return_value=True),
        ):
            with self.assertRaisesRegex(RunnerError, "Offline secure builds"):
                _prepare_runtime_image(
                    b"requests==2.32.5",
                    "test:latest",
                    Path(folder) / "context",
                    Path(folder),
                    cache_namespace="test",
                )


class ImageArchiveBudgetTests(unittest.TestCase):
    def archive(self, path, *, size=1, tag="test:latest"):
        entry = tarfile.TarInfo("app")
        entry.size = size
        layer = entry.tobuf() + (b"x" + b"\0" * 511 if size == 1 else b"") + b"\0" * 1024
        manifest = json.dumps([{"RepoTags": [tag], "Layers": ["layer.tar"]}]).encode()
        with tarfile.open(path, "w") as outer:
            for name, content in [("manifest.json", manifest), ("layer.tar", layer)]:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                outer.addfile(info, io.BytesIO(content))

    def test_small_valid_image_is_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            self.archive(path)
            build.validate_image_archive(path, "test:latest")

    def test_sparse_huge_logical_file_rejected_without_unpacking(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            self.archive(path, size=build.MAX_IMAGE_EXPANDED + 1)
            with self.assertRaises(RunnerError):
                build.validate_image_archive(path, "test:latest")

    def test_unexpected_image_tag_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            self.archive(path, tag="unrelated:latest")
            with self.assertRaises(RunnerError):
                build.validate_image_archive(path, "test:latest")

    def test_duplicate_tar_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image.tar"
            self.archive(path)
            with tarfile.open(path, "a") as outer:
                info = tarfile.TarInfo("manifest.json")
                info.size = 2
                outer.addfile(info, io.BytesIO(b"[]"))
            with self.assertRaisesRegex(RunnerError, "Ambiguous"):
                build.validate_image_archive(path, "test:latest")

    def test_child_process_file_limit_is_actually_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bounded"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "selfad.runtime.exec_limit",
                    "1024",
                    sys.executable,
                    "-c",
                    'import sys\nwith open(sys.argv[1],"wb") as stream:\n stream.write(b"x"*4096)',
                    str(path),
                ],
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertLessEqual(path.stat().st_size, 1024)


class LiveProbeSafetyTests(unittest.TestCase):
    def test_probe_programs_compile_and_are_resource_bounded(self):
        compile(security_check.RUNTIME_PROBE, "<runtime probe>", "exec")
        compile(security_check.PID_PROBE, "<pid probe>", "exec")
        args = security_check.options("isolated", "selfad-owned-test")
        for expected in ("--read-only", "ALL", "no-new-privileges", "core=0:0", "max-size=5m"):
            self.assertIn(expected, args)
        for prohibited in ("--privileged", "--volume", "--mount", "--publish"):
            self.assertNotIn(prohibited, args)
