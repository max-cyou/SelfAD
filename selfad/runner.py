import io
import os
import re
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from selfad.database import DATA_DIR
from selfad.gitea import download_repository_archive
from selfad.service_contract import ServiceContractResult
from selfad.settings import GiteaSettings


WORK_DIR = DATA_DIR / "work"
DOCKER_HOST = "unix:///run/selfad-docker/docker.sock"
JURY_IMAGE = "python:3.13-alpine"
MAX_ARCHIVE_FILES = 2_000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
MAX_COMMAND_OUTPUT = 128 * 1024
DEFAULT_FLAG_PATTERN = re.compile(r"^[A-Z0-9]{32}$")


class RunnerError(Exception):
    pass


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str


@dataclass(frozen=True)
class RuntimeCheckResult:
    passed: bool
    functionality_passed: bool
    message: str
    matched_flags: int
    log: str


def runner_is_available() -> bool:
    try:
        result = _docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    except RunnerError:
        return False
    return result.returncode == 0


def run_service_runtime_check(
    settings: GiteaSettings,
    *,
    repository_path: str,
    jury_repository_path: str,
    contract: ServiceContractResult,
    exploit_repository_path: str | None = None,
    exploit_commit: str | None = None,
) -> RuntimeCheckResult:
    if (
        not contract.valid
        or not contract.source_commit
        or not contract.jury_commit
        or not contract.container_port
        or not contract.healthcheck_path
    ):
        raise RunnerError("A valid repository contract is required.")
    if not runner_is_available():
        raise RunnerError(
            "The internal Docker runner is unavailable. Start SelfAD with --privileged."
        )

    source_archive = download_repository_archive(
        settings,
        repository_path,
        ref=contract.source_commit,
    )
    jury_archive = download_repository_archive(
        settings,
        jury_repository_path,
        ref=contract.jury_commit,
    )
    if exploit_repository_path and exploit_commit:
        exploit_archive = download_repository_archive(
            settings,
            exploit_repository_path,
            ref=exploit_commit,
        )
    else:
        exploit_archive = jury_archive

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    image_name = f"selfad-check-{job_id}:latest"
    network_name = f"selfad-check-{job_id}"
    service_name = f"selfad-service-{job_id}"
    injector_name = f"selfad-inject-{job_id}"
    exploit_name = f"selfad-exploit-{job_id}"
    build_log = ""

    with tempfile.TemporaryDirectory(prefix=f"check-{job_id}-", dir=WORK_DIR) as job:
        job_path = Path(job)
        source_path = job_path / "service"
        jury_path = job_path / "jury"
        exploit_path = job_path / "exploit"
        _extract_repository_archive(source_archive, source_path)
        _extract_repository_archive(jury_archive, jury_path)
        _extract_repository_archive(exploit_archive, exploit_path)

        try:
            functionality_passed = False
            build = _docker(
                [
                    "build",
                    "--tag",
                    image_name,
                    str(source_path),
                ],
                timeout=300,
                max_output=2 * 1024 * 1024,
            )
            build_log = build.output
            if build.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Service image build failed.",
                    0,
                    _format_log("build", build.output),
                )

            network = _docker(
                ["network", "create", "--internal", network_name],
                timeout=30,
            )
            if network.returncode != 0:
                raise RunnerError(f"Could not create the runner network: {network.output}")

            service = _docker(
                [
                    "run",
                    "--detach",
                    "--name",
                    service_name,
                    "--network",
                    network_name,
                    "--network-alias",
                    "target",
                    "--memory",
                    "256m",
                    "--memory-swap",
                    "256m",
                    "--cpus",
                    "1",
                    "--pids-limit",
                    "128",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m",
                    "--tmpfs",
                    "/run:rw,noexec,nosuid,size=16m",
                    image_name,
                ],
                timeout=30,
            )
            if service.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Service container failed to start.",
                    0,
                    _format_log("docker run", service.output),
                )

            container_ip = _container_ip(service_name, network_name)
            _wait_for_healthcheck(
                container_ip,
                contract.container_port,
                contract.healthcheck_path,
            )

            target = f"http://target:{contract.container_port}"
            injector = _run_jury_script(
                container_name=injector_name,
                network_name=network_name,
                jury_path=jury_path,
                script_name="inject.py",
                target=target,
            )
            if injector.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Jury injector failed.",
                    0,
                    _format_log("inject.py", injector.output),
                )

            expected_flags = _output_tokens(injector.output)
            if not expected_flags:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Jury injector produced no flags on stdout.",
                    0,
                    _format_log("inject.py", injector.output),
                )

            functionality_passed = True
            exploit = _run_jury_script(
                container_name=exploit_name,
                network_name=network_name,
                jury_path=exploit_path,
                script_name="exploit.py",
                target=target,
            )
            if exploit.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    True,
                    "Jury exploit failed.",
                    0,
                    _join_logs(injector.output, exploit.output),
                )

            recovered_flags = _output_tokens(exploit.output)
            matched_flags = len(expected_flags & recovered_flags)
            passed = matched_flags > 0
            message = (
                f"Runtime check passed: recovered {matched_flags} of "
                f"{len(expected_flags)} injected flags."
                if passed
                else "Runtime check failed: the exploit recovered no injected flags."
            )
            return RuntimeCheckResult(
                passed,
                True,
                message,
                matched_flags,
                _join_logs(injector.output, exploit.output),
            )
        except RunnerError as error:
            return RuntimeCheckResult(
                False,
                functionality_passed,
                str(error),
                0,
                _format_log("build", build_log),
            )
        finally:
            for container_name in (exploit_name, injector_name, service_name):
                _docker_quiet(["rm", "--force", container_name])
            _docker_quiet(["network", "rm", network_name])
            _docker_quiet(["image", "rm", "--force", image_name])


def _extract_repository_archive(archive_bytes: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_size = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except tarfile.TarError as error:
        raise RunnerError("Gitea returned an invalid repository archive.") from error

    with archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
                if len(path.parts) < 2 and member.isdir():
                    continue
                raise RunnerError("Repository archive contains an unsafe path.")
            relative_parts = path.parts[1:]
            if not relative_parts:
                continue
            target = destination.joinpath(*relative_parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise RunnerError("Repository archive contains unsupported file types.")

            file_count += 1
            total_size += member.size
            if file_count > MAX_ARCHIVE_FILES or total_size > MAX_EXTRACTED_BYTES:
                raise RunnerError("Repository archive exceeds runner limits.")
            source = archive.extractfile(member)
            if source is None:
                raise RunnerError("Repository archive contains an unreadable file.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def _run_jury_script(
    *,
    container_name: str,
    network_name: str,
    jury_path: Path,
    script_name: str,
    target: str,
) -> CommandResult:
    image = _docker(["image", "inspect", JURY_IMAGE], timeout=15)
    if image.returncode != 0:
        pull = _docker(["pull", JURY_IMAGE], timeout=300, max_output=2 * 1024 * 1024)
        if pull.returncode != 0:
            raise RunnerError("Could not prepare the fixed jury runtime image.")

    return _docker(
        [
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            network_name,
            "--memory",
            "128m",
            "--memory-swap",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--volume",
            f"{jury_path}:/workspace:ro",
            "--workdir",
            "/workspace",
            "--env",
            f"SELFAD_TARGET={target}",
            JURY_IMAGE,
            "python",
            script_name,
            target,
        ],
        timeout=45,
        max_output=MAX_COMMAND_OUTPUT,
    )


def _container_ip(container_name: str, network_name: str) -> str:
    result = _docker(
        [
            "inspect",
            "--format",
            f"{{{{(index .NetworkSettings.Networks \"{network_name}\").IPAddress}}}}",
            container_name,
        ],
        timeout=15,
    )
    address = result.output.strip()
    if result.returncode != 0 or not address:
        raise RunnerError("Could not resolve the service container address.")
    return address


def _wait_for_healthcheck(address: str, port: int, path: str) -> None:
    url = f"http://{address}:{port}{path}"
    deadline = time.monotonic() + 45
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 400:
                    return
                last_error = f"HTTP {response.status}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = str(error)
        time.sleep(1)
    raise RunnerError(f"Service healthcheck failed: {last_error}")


def _output_tokens(output: str) -> set[str]:
    return {
        line.strip()
        for line in output.splitlines()
        if DEFAULT_FLAG_PATTERN.fullmatch(line.strip())
    }


def _join_logs(injector_output: str, exploit_output: str) -> str:
    return (
        _format_log("inject.py", injector_output)
        + "\n"
        + _format_log("exploit.py", exploit_output)
    )[:MAX_COMMAND_OUTPUT]


def _format_log(label: str, output: str) -> str:
    return f"[{label}]\n{output.strip()}"[:MAX_COMMAND_OUTPUT]


def _docker(
    arguments: list[str],
    *,
    timeout: int,
    max_output: int = MAX_COMMAND_OUTPUT,
) -> CommandResult:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="docker-cli-", dir=WORK_DIR) as home:
        environment = os.environ.copy()
        environment["DOCKER_HOST"] = DOCKER_HOST
        environment["HOME"] = home
        environment["DOCKER_CONFIG"] = str(Path(home) / ".docker")
        return _run_command(
            ["docker", *arguments],
            timeout=timeout,
            max_output=max_output,
            environment=environment,
        )


def _docker_quiet(arguments: list[str]) -> None:
    try:
        _docker(arguments, timeout=30, max_output=32 * 1024)
    except RunnerError:
        pass


def _run_command(
    arguments: list[str],
    *,
    timeout: int,
    max_output: int,
    environment: dict[str, str],
) -> CommandResult:
    try:
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
        )
    except OSError as error:
        raise RunnerError(f"Could not start {arguments[0]}.") from error
    assert process.stdout is not None

    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    failure: str | None = None
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                failure = f"Command timed out after {timeout} seconds."
                break
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > max_output:
                    failure = f"Command output exceeded {max_output} bytes."
                    break
            if failure:
                break
        if failure:
            process.kill()
            process.wait(timeout=5)
            raise RunnerError(failure)
        returncode = process.wait(timeout=5)
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    return CommandResult(
        returncode,
        output.decode("utf-8", errors="replace"),
    )
