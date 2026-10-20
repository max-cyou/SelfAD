"""Bounded public-event builds; no fallback to the unrestricted Docker builder."""

import json
import os
import re
import tarfile
import tempfile
import threading
from pathlib import Path

from selfad.runtime.docker import _docker
from selfad.runtime.types import WORK_DIR, RunnerError, RunnerInfrastructureError
from selfad.settings import get_runner_settings, public_event_mode_enabled

BUILD_MEMORY = 4 * 1024**3
BUILD_PIDS = 512
MAX_IMAGE_ARCHIVE = 512 * 1024**2
MAX_IMAGE_EXPANDED = 1024**3
BUILD_LOCK = threading.Lock()
MAX_RUNNER_IMAGES = 16 * 1024**3
BUILD_FIREWALL_CHECK = """set -eu
for tool in iptables ip6tables; do
    test "$("$tool" -t mangle -S FORWARD | sed -n '2p')" = '-A FORWARD -j DROP'
    "$tool" -t mangle -C INPUT ! -i lo -m conntrack --ctstate NEW,INVALID,UNTRACKED -j DROP
done
"""
BUILD_FIREWALL_INSTALL = """set -eu
for tool in iptables ip6tables; do
    if "$tool" -t mangle -C FORWARD -j DROP 2>/dev/null; then
        "$tool" -t mangle -D FORWARD -j DROP
    fi
    if "$tool" -t mangle -C INPUT ! -i lo -m conntrack --ctstate NEW,INVALID,UNTRACKED -j DROP 2>/dev/null; then
        "$tool" -t mangle -D INPUT ! -i lo -m conntrack --ctstate NEW,INVALID,UNTRACKED -j DROP
    fi
    "$tool" -t mangle -I FORWARD 1 -j DROP
    "$tool" -t mangle -I INPUT 1 ! -i lo -m conntrack --ctstate NEW,INVALID,UNTRACKED -j DROP
done
"""


def builder_name():
    name = os.getenv("SELFAD_RUNNER_BUILDER", "selfad-secure")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,39}", name):
        raise RunnerError("Invalid SELFAD_RUNNER_BUILDER name.")
    return name


def _json(arguments):
    result = _docker(arguments, timeout=30)
    if result.returncode:
        raise RunnerError(
            "Secure builder is unavailable; initialize it before accepting submissions."
        )
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RunnerError("Invalid builder inspection response.") from error


def verify_builder():
    try:
        return _verify_builder()
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise RunnerError("Malformed secure builder inspection response.") from error


def _verify_builder(require_firewall=True):
    name = builder_name()
    metadata = _json(["buildx", "inspect", name, "--format", "{{json .}}"])
    if metadata.get("Driver") != "docker-container" or len(metadata.get("Nodes", [])) != 1:
        raise RunnerError("Secure builds require a single docker-container builder.")
    node = metadata["Nodes"][0]
    if node.get("Name") != name + "0" or node.get("Endpoint") != name + "-runner":
        raise RunnerError("BuildKit node/endpoint differs from the inspected runner.")
    endpoint = _json(["context", "inspect", name + "-runner"])[0]["Endpoints"]["docker"]
    if endpoint["Host"] != get_runner_settings().docker_host or endpoint.get("SkipTLSVerify"):
        raise RunnerError("BuildKit context does not match the verified mTLS runner endpoint.")
    container = _json(["inspect", f"buildx_buildkit_{name}0"])[0]
    if not container.get("State", {}).get("Running"):
        raise RunnerError("Secure builder is not running.")
    host = container["HostConfig"]
    memory, swap = host.get("Memory", 0), host.get("MemorySwap", 0)
    cpu = host.get("NanoCpus", 0) / 1e9
    if not cpu and host.get("CpuPeriod", 0) > 0:
        cpu = host.get("CpuQuota", 0) / host["CpuPeriod"]
    if not (
        0 < memory <= BUILD_MEMORY
        and swap == memory
        and 0 < cpu <= 2
        and 0 < host.get("PidsLimit", 0) <= BUILD_PIDS
    ):
        raise RunnerError("BuildKit CPU, RAM, swap or PID limits are missing/unsafe.")
    if host.get("NetworkMode") == "host" or host.get("PidMode") == "host":
        raise RunnerError("BuildKit must not share host networking/PID namespace.")
    if host.get("CgroupnsMode") != "private":
        raise RunnerError("BuildKit requires a private cgroup v2 namespace.")
    command = (container.get("Args") or []) + (container.get("Config", {}).get("Cmd") or [])
    if any("allow-insecure-entitlement" in item for item in command):
        raise RunnerError("BuildKit insecure entitlements must be disabled.")
    if "--oci-worker-net=bridge" not in command:
        raise RunnerError("BuildKit must explicitly use isolated bridge build namespaces.")
    if require_firewall:
        firewall = _docker(
            ["exec", f"buildx_buildkit_{name}0", "sh", "-c", BUILD_FIREWALL_CHECK], timeout=10
        )
        if firewall.returncode:
            raise RunnerError(
                "BuildKit network firewall is missing; reinitialize before accepting jobs."
            )
    mounts = container.get("Mounts", [])
    state = [mount for mount in mounts if mount.get("Destination") == "/var/lib/buildkit"]
    if len(mounts) != 1 or len(state) != 1 or state[0].get("Type") != "volume":
        raise RunnerError("BuildKit must use bounded tmpfs state, without host bind mounts.")
    volume = _json(["volume", "inspect", state[0]["Name"]])[0]
    options = volume.get("Options") or {}
    tokens = options.get("o", "").split(",")
    if (
        volume.get("Driver") != "local"
        or options.get("type") != "tmpfs"
        or options.get("device") != "tmpfs"
        or tokens != [f"size={BUILD_MEMORY}"]
    ):
        raise RunnerError("BuildKit state volume must be size-limited tmpfs.")
    limits = _docker(
        [
            "exec",
            f"buildx_buildkit_{name}0",
            "sh",
            "-c",
            "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/pids.max /sys/fs/cgroup/cpu.max",
        ],
        timeout=10,
    )
    fields = limits.stdout.split()
    if (
        limits.returncode
        or len(fields) != 5
        or fields[0] != str(memory)
        or fields[1] != "0"
        or fields[2] != str(host["PidsLimit"])
        or not fields[3].isdigit()
        or not fields[4].isdigit()
        or int(fields[4]) <= 0
        or not (0 < int(fields[3]) / int(fields[4]) <= 2)
    ):
        raise RunnerError("Kernel cgroup limits do not match the secure builder configuration.")
    return container


def check_image_budget():
    listed = _docker(["image", "ls", "--no-trunc", "--quiet"], timeout=30)
    ids = sorted(set(listed.stdout.split()))
    if (
        listed.returncode
        or len(ids) > 256
        or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", id) for id in ids)
    ):
        raise RunnerInfrastructureError("Cannot verify runner image storage budget.")
    if ids:
        sizes = _docker(["image", "inspect", "--format", "{{.Size}}", *ids], timeout=30)
        lines = sizes.stdout.split()
        if sizes.returncode or len(lines) != len(ids) or any(not line.isdigit() for line in lines):
            raise RunnerInfrastructureError("Cannot verify runner image sizes.")
        if sum(map(int, lines)) + MAX_IMAGE_EXPANDED > MAX_RUNNER_IMAGES:
            raise RunnerInfrastructureError(
                "Runner image budget exhausted; operator cleanup required."
            )


def validate_image_archive(path, tag):
    """Bound logical layer sizes before Docker loads/unpacks an exported image."""
    if path.stat().st_size > MAX_IMAGE_ARCHIVE:
        raise RunnerError("Built image archive exceeds 512 MiB.")
    try:
        with tarfile.open(path, "r:") as outer:
            names = set()
            outer_size = 0
            for member in outer:
                outer_size += member.size
                if (
                    member.name in names
                    or len(names) >= 10000
                    or member.size < 0
                    or outer_size > MAX_IMAGE_ARCHIVE
                    or not (member.isfile() or member.isdir())
                ):
                    raise RunnerError("Ambiguous or oversized image archive entries.")
                names.add(member.name)
            manifest_file = outer.getmember("manifest.json")
            if manifest_file.size > 65536:
                raise RunnerError("Oversized image manifest.")
            with outer.extractfile(manifest_file) as stream:
                manifest = json.load(stream)
            tags = manifest[0].get("RepoTags") if len(manifest) == 1 else None
            allowed = {tag, "docker.io/library/" + tag} if "/" not in tag else {tag}
            if not isinstance(tags, list) or len(tags) != 1 or tags[0] not in allowed:
                raise RunnerError("Unexpected image tags in build output.")
            total = count = 0
            for name in manifest[0]["Layers"]:
                member = outer.getmember(name)
                if not member.isfile():
                    raise RunnerError("Invalid image layer.")
                with (
                    outer.extractfile(member) as stream,
                    tarfile.open(fileobj=stream, mode="r|*") as layer,
                ):
                    for entry in layer:
                        if entry.size < 0:
                            raise RunnerError("Invalid layer file size.")
                        total += entry.size
                        count += 1
                        if total > MAX_IMAGE_EXPANDED or count > 100000:
                            raise RunnerError("Built image exceeds 1 GiB expanded / 100000 files.")
    except (tarfile.TarError, KeyError, ValueError, TypeError, AttributeError, OSError) as error:
        raise RunnerError("Invalid built image archive.") from error


def build_image(context: Path, tag: str, labels: list[str]):
    if not public_event_mode_enabled() and not os.getenv("SELFAD_RUNNER_BUILDER"):
        # Trusted local development only; production can never select this path.
        return _docker(
            ["build", *labels, "--tag", tag, str(context)], timeout=300, max_output=2 * 1024**2
        )
    with BUILD_LOCK:
        try:
            verify_builder()
            check_image_budget()
        except RunnerError as error:
            raise RunnerInfrastructureError(str(error)) from error
        with tempfile.TemporaryDirectory(prefix="image-export-", dir=WORK_DIR) as directory:
            archive = Path(directory) / "image.tar"
            result = _docker(
                [
                    "buildx",
                    "build",
                    "--builder",
                    builder_name(),
                    "--network=none",
                    "--cgroup-parent=/selfad-builds",
                    "--resource",
                    "memory=4g",
                    "--resource",
                    "memory-swap=4g",
                    "--resource",
                    "cpu-period=100000",
                    "--resource",
                    "cpu-quota=200000",
                    "--ulimit",
                    "nofile=1024:1024",
                    "--ulimit",
                    "core=0:0",
                    "--ulimit",
                    "fsize=1073741824:1073741824",
                    "--output",
                    f"type=docker,dest={archive}",
                    *labels,
                    "--tag",
                    tag,
                    str(context),
                ],
                timeout=300,
                max_output=2 * 1024**2,
                file_limit=MAX_IMAGE_ARCHIVE,
            )
            if result.returncode:
                return result
            validate_image_archive(archive, tag)
            loaded = _docker(["load", "--input", str(archive)], timeout=120)
            return result if loaded.returncode == 0 else loaded


def initialize_builder():
    """Explicit operator action on the disposable runner, not called by jobs."""
    name = builder_name()
    context = name + "-runner"
    context_result = _docker(["context", "inspect", context], timeout=30)
    if context_result.returncode:
        created = _docker(["context", "create", context], timeout=30)
        if created.returncode:
            raise RunnerError(created.output)
    endpoint = _json(["context", "inspect", context])[0]["Endpoints"]["docker"]
    if endpoint["Host"] != get_runner_settings().docker_host or endpoint.get("SkipTLSVerify"):
        raise RunnerError("Refusing an existing context for a different or insecure runner.")
    existing = _docker(["inspect", f"buildx_buildkit_{name}0"], timeout=30)
    if existing.returncode == 0:
        _verify_builder(require_firewall=False)  # Never exec firewall rules in a host namespace.
        restored = _docker(
            ["exec", f"buildx_buildkit_{name}0", "sh", "-c", BUILD_FIREWALL_INSTALL], timeout=10
        )
        if restored.returncode:
            raise RunnerError(restored.output)
        verify_builder()  # Never overwrite an existing unsafe configuration.
        return
    volume = f"buildx_buildkit_{name}0_state"
    result = _docker(
        [
            "volume",
            "create",
            "--driver",
            "local",
            "--opt",
            "type=tmpfs",
            "--opt",
            "device=tmpfs",
            "--opt",
            f"o=size={BUILD_MEMORY}",
            volume,
        ],
        timeout=30,
    )
    if result.returncode:
        raise RunnerError(result.output)
    inspected = _json(["volume", "inspect", volume])[0]
    if inspected.get("Driver") != "local" or inspected.get("Options") != {
        "type": "tmpfs",
        "device": "tmpfs",
        "o": f"size={BUILD_MEMORY}",
    }:
        raise RunnerError("Refusing an existing unbounded BuildKit state volume.")
    result = _docker(
        [
            "buildx",
            "create",
            "--name",
            name,
            "--driver",
            "docker-container",
            "--driver-opt",
            "memory=4g",
            "--driver-opt",
            "image=moby/buildkit:v0.33.1",
            "--driver-opt",
            "memory-swap=4g",
            "--driver-opt",
            "cpu-period=100000",
            "--driver-opt",
            "cpu-quota=200000",
            "--buildkitd-flags",
            "--oci-worker-net=bridge --oci-worker-snapshotter=native",
            "--node",
            name + "0",
            "--bootstrap",
            context,
        ],
        timeout=300,
    )
    if result.returncode:
        raise RunnerError(result.output)
    updated = _docker(
        ["update", "--pids-limit", str(BUILD_PIDS), f"buildx_buildkit_{name}0"], timeout=30
    )
    if updated.returncode:
        raise RunnerError(updated.output)
    firewall = _docker(
        ["exec", f"buildx_buildkit_{name}0", "sh", "-c", BUILD_FIREWALL_INSTALL], timeout=10
    )
    if firewall.returncode:
        raise RunnerError(firewall.output)
    verify_builder()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args()
    initialize_builder() if args.initialize else verify_builder()
