import io
import os
import re
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from selfad.database import DATA_DIR
from selfad.gitea import download_repository_archive
from selfad.service_contract import ServiceContractResult
from selfad.settings import GiteaSettings, get_runner_settings


WORK_DIR = DATA_DIR / "work"
JURY_IMAGE = "python:3.13-alpine"
MAX_ARCHIVE_FILES = 2_000
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024
MAX_COMMAND_OUTPUT = 128 * 1024
MAX_REQUIREMENTS_BYTES = 64 * 1024
DEFAULT_FLAG_PATTERN = re.compile(r"^[A-Z0-9]{32}$")
PARTICIPANT_REQUIREMENT_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[A-Za-z0-9][A-Za-z0-9_.+!~-]*$"
)
RUNNER_LABEL = "selfad.managed=true"
RUNNER_USER = "10001:10001"


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
    injected_flags: int = 0
    completed: bool = False


def runner_is_available() -> bool:
    try:
        result = _docker(["info", "--format", "{{.ServerVersion}}"], timeout=10)
    except RunnerError:
        return False
    return result.returncode == 0


def runner_mode() -> str:
    settings = get_runner_settings()
    if settings.uses_internal_runner:
        return "internal" if settings.internal_runner_enabled else "unavailable"
    return "external"


def cleanup_managed_runner_resources() -> int:
    """Remove job resources left behind by a stopped SelfAD process.

    Every job object receives ``selfad.managed=true``. This deliberately never
    touches runner resources without that label.
    """
    if not runner_is_available():
        return 0
    removed = 0
    resource_commands = (
        ("ps", "-aq", "--filter", f"label={RUNNER_LABEL}"),
        ("network", "ls", "-q", "--filter", f"label={RUNNER_LABEL}"),
        ("images", "-q", "--filter", f"label={RUNNER_LABEL}"),
    )
    for index, command in enumerate(resource_commands):
        result = _docker(list(command), timeout=30, max_output=32 * 1024)
        if result.returncode != 0:
            continue
        identifiers = [
            line.strip() for line in result.output.splitlines() if line.strip()
        ]
        if not identifiers:
            continue
        if index == 0:
            _docker_quiet(["rm", "--force", *identifiers])
        elif index == 1:
            _docker_quiet(["network", "rm", *identifiers])
        else:
            _docker_quiet(["image", "rm", "--force", *identifiers])
        removed += len(identifiers)
    return removed


def run_service_runtime_check(
    settings: GiteaSettings,
    *,
    repository_path: str,
    jury_repository_path: str,
    contract: ServiceContractResult,
    exploit_repository_path: str | None = None,
    exploit_commit: str | None = None,
    exploit_runtime_requirements: bytes | None = None,
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
            "The configured Docker runner is unavailable."
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
    jury_image_name = f"selfad-jury-{job_id}:latest"
    exploit_image_name = f"selfad-attack-{job_id}:latest"
    network_name = f"selfad-check-{job_id}"
    service_name = f"selfad-service-{job_id}"
    injector_name = f"selfad-inject-{job_id}"
    exploit_name = f"selfad-exploit-{job_id}"
    build_log = ""
    checker_output = ""

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
                    "--label",
                    RUNNER_LABEL,
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

            jury_requirements = _read_requirements_file(
                jury_path / "requirements.txt",
                label="Jury requirements.txt",
            )
            jury_image, jury_build = _prepare_runtime_image(
                jury_requirements,
                jury_image_name,
                job_path / "jury-runtime",
            )
            if jury_build is not None and jury_build.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Jury dependency installation failed.",
                    0,
                    _format_log("jury dependencies", jury_build.output),
                )

            exploit_image = jury_image
            if exploit_repository_path:
                exploit_image, exploit_build = _prepare_runtime_image(
                    exploit_runtime_requirements,
                    exploit_image_name,
                    job_path / "attack-runtime",
                )
                if exploit_build is not None and exploit_build.returncode != 0:
                    return RuntimeCheckResult(
                        False,
                        False,
                        "Attack dependency installation failed.",
                        0,
                        _format_log("attack dependencies", exploit_build.output),
                    )

            network = _docker(
                [
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    RUNNER_LABEL,
                    network_name,
                ],
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
                    "--label",
                    RUNNER_LABEL,
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
                    "--ulimit",
                    "nofile=256:256",
                    "--init",
                    "--ipc",
                    "none",
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

            _wait_for_healthcheck(
                network_name,
                contract.container_port,
                contract.healthcheck_path,
            )

            target = f"http://target:{contract.container_port}"
            if (jury_path / "checker.py").is_file():
                checker = _run_jury_script(
                    container_name=f"selfad-checker-{job_id}",
                    network_name=network_name,
                    jury_path=jury_path,
                    script_name="checker.py",
                    target=target,
                    image=jury_image,
                )
                checker_output = checker.output
                if checker.returncode != 0:
                    return RuntimeCheckResult(
                        False,
                        False,
                        _script_failure_message(
                            "Functionality checker",
                            checker.output,
                        ),
                        0,
                        _join_logs(("checker.py", checker.output)),
                    )

            injector = _run_jury_script(
                container_name=injector_name,
                network_name=network_name,
                jury_path=jury_path,
                script_name="inject.py",
                target=target,
                image=jury_image,
            )
            if injector.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Jury injector failed.",
                    0,
                    _join_logs(
                        ("checker.py", checker_output),
                        ("inject.py", injector.output),
                    ),
                )

            expected_flags = _output_tokens(injector.output)
            if not expected_flags:
                return RuntimeCheckResult(
                    False,
                    False,
                    "Jury injector produced no flags on stdout.",
                    0,
                    _join_logs(
                        ("checker.py", checker_output),
                        ("inject.py", injector.output),
                    ),
                )

            functionality_passed = True
            exploit = _run_jury_script(
                container_name=exploit_name,
                network_name=network_name,
                jury_path=exploit_path,
                script_name="exploit.py",
                target=target,
                image=exploit_image,
            )
            if exploit.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    True,
                    "Jury exploit failed.",
                    0,
                    _join_logs(
                        ("checker.py", checker_output),
                        ("inject.py", injector.output),
                        ("exploit.py", exploit.output),
                    ),
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
                _join_logs(
                    ("checker.py", checker_output),
                    ("inject.py", injector.output),
                    ("exploit.py", exploit.output),
                ),
                len(expected_flags),
                True,
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
            for container_name in (
                exploit_name,
                injector_name,
                f"selfad-checker-{job_id}",
                service_name,
            ):
                _docker_quiet(["rm", "--force", container_name])
            _docker_quiet(["network", "rm", network_name])
            _docker_quiet(["image", "rm", "--force", image_name])
            _docker_quiet(["image", "rm", "--force", jury_image_name])
            _docker_quiet(["image", "rm", "--force", exploit_image_name])


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
    image: str,
) -> CommandResult:
    return _docker(
        [
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            RUNNER_LABEL,
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
            "--ulimit",
            "nofile=128:128",
            "--init",
            "--ipc",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--user",
            RUNNER_USER,
            "--volume",
            f"{jury_path}:/workspace:ro",
            "--workdir",
            "/workspace",
            "--env",
            f"SELFAD_TARGET={target}",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            image,
            "python",
            script_name,
            target,
        ],
        timeout=45,
        max_output=MAX_COMMAND_OUTPUT,
    )


def _read_requirements_file(path: Path, *, label: str) -> bytes | None:
    if not path.is_file():
        return None
    content = path.read_bytes()
    _validate_requirements(content, label=label)
    return content


def _validate_requirements(content: bytes, *, label: str) -> None:
    if len(content) > MAX_REQUIREMENTS_BYTES:
        raise RunnerError(f"{label} exceeds the 64 KB limit.")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RunnerError(f"{label} must be UTF-8 text.") from error
    if "\x00" in decoded:
        raise RunnerError(f"{label} must not contain null bytes.")


def build_attack_runtime_requirements(
    fixed_requirements: str,
    participant_requirements: bytes | None,
) -> bytes | None:
    fixed = fixed_requirements.encode("utf-8")
    _validate_requirements(fixed, label="Fixed attack requirements")
    if participant_requirements is not None:
        _validate_participant_requirements(participant_requirements)

    parts = [content for content in (fixed, participant_requirements) if content]
    if not parts:
        return None
    combined = b"\n".join(parts)
    _validate_requirements(combined, label="Combined attack requirements")
    return combined


def _validate_participant_requirements(content: bytes) -> None:
    _validate_requirements(content, label="Participant requirements.txt")
    for line in content.decode("utf-8").splitlines():
        requirement = line.split("#", 1)[0].strip()
        if requirement and not PARTICIPANT_REQUIREMENT_PATTERN.fullmatch(
            requirement
        ):
            raise RunnerError(
                "Participant requirements.txt only accepts pinned "
                "package==version lines."
            )


def _prepare_runtime_image(
    requirements: bytes | None,
    image_name: str,
    context: Path,
) -> tuple[str, CommandResult | None]:
    base_image = _docker(["image", "inspect", JURY_IMAGE], timeout=15)
    if base_image.returncode != 0:
        pull = _docker(
            ["pull", JURY_IMAGE],
            timeout=300,
            max_output=2 * 1024 * 1024,
        )
        if pull.returncode != 0:
            raise RunnerError("Could not prepare the fixed jury runtime image.")

    if not requirements or not requirements.strip():
        return JURY_IMAGE, None

    _validate_requirements(requirements, label="Requirements")
    context.mkdir()
    (context / "requirements.txt").write_bytes(requirements)
    (context / "Dockerfile").write_text(
        "FROM python:3.13-alpine\n"
        "COPY requirements.txt /tmp/selfad-requirements.txt\n"
        "RUN python -m pip install --no-cache-dir --disable-pip-version-check "
        "-r /tmp/selfad-requirements.txt "
        "&& rm /tmp/selfad-requirements.txt\n",
        encoding="utf-8",
    )
    build = _docker(
        ["build", "--label", RUNNER_LABEL, "--tag", image_name, str(context)],
        timeout=300,
        max_output=2 * 1024 * 1024,
    )
    return image_name, build


def _wait_for_healthcheck(network_name: str, port: int, path: str) -> None:
    target = f"http://target:{port}{path}"
    probe_code = (
        "import sys,time,urllib.request,urllib.error\n"
        "url=sys.argv[1]; last='no response'; deadline=time.monotonic()+45\n"
        "while time.monotonic()<deadline:\n"
        "  try:\n"
        "    with urllib.request.urlopen(url,timeout=2) as response:\n"
        "      if 200<=response.status<400: sys.exit(0)\n"
        "      last=f'HTTP {response.status}'\n"
        "  except (urllib.error.URLError,TimeoutError,OSError) as error: last=str(error)\n"
        "  time.sleep(1)\n"
        "print(last); sys.exit(1)\n"
    )
    probe = _docker(
        [
            "run",
            "--rm",
            "--label",
            RUNNER_LABEL,
            "--network",
            network_name,
            "--memory",
            "64m",
            "--memory-swap",
            "64m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "32",
            "--ulimit",
            "nofile=64:64",
            "--init",
            "--ipc",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=8m",
            "--user",
            RUNNER_USER,
            "--env",
            "HOME=/tmp",
            JURY_IMAGE,
            "python",
            "-c",
            probe_code,
            target,
        ],
        timeout=55,
        max_output=16 * 1024,
    )
    if probe.returncode != 0:
        detail = probe.output.strip() or "no response"
        raise RunnerError(f"Service healthcheck failed: {detail}")


def _output_tokens(output: str) -> set[str]:
    return {
        line.strip()
        for line in output.splitlines()
        if DEFAULT_FLAG_PATTERN.fullmatch(line.strip())
    }


def _join_logs(*logs: tuple[str, str]) -> str:
    return "\n".join(
        _format_log(label, output)
        for label, output in logs
        if output.strip()
    )[:MAX_COMMAND_OUTPUT]


def _script_failure_message(label: str, output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    detail = lines[-1][:500] if lines else "exited with a non-zero status"
    return f"{label} failed: {detail}"


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
        settings = get_runner_settings()
        environment = os.environ.copy()
        environment["DOCKER_HOST"] = settings.docker_host
        if settings.tls_verify:
            environment["DOCKER_TLS_VERIFY"] = "1"
        else:
            environment.pop("DOCKER_TLS_VERIFY", None)
        if settings.cert_path:
            environment["DOCKER_CERT_PATH"] = settings.cert_path
        else:
            environment.pop("DOCKER_CERT_PATH", None)
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
