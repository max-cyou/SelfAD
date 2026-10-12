import os
import tempfile
from pathlib import Path

from selfad.runtime.process import (
    _run_command,
)
from selfad.runtime.types import (
    MAX_COMMAND_OUTPUT,
    RUNNER_LABEL,
    WORK_DIR,
    CommandResult,
    RunnerError,
)
from selfad.settings import (
    get_runner_instance_id,
    get_runner_settings,
)


def _instance_label() -> str:
    return f"selfad.instance={get_runner_instance_id()}"


def _label_arguments(*labels: str) -> list[str]:
    arguments: list[str] = []
    for label in (*labels, _instance_label()):
        arguments.extend(("--label", label))
    return arguments


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
        (
            "ps",
            "-aq",
            "--filter",
            f"label={RUNNER_LABEL}",
            "--filter",
            f"label={_instance_label()}",
        ),
        (
            "network",
            "ls",
            "-q",
            "--filter",
            f"label={RUNNER_LABEL}",
            "--filter",
            f"label={_instance_label()}",
        ),
        (
            "images",
            "-q",
            "--filter",
            f"label={RUNNER_LABEL}",
            "--filter",
            f"label={_instance_label()}",
        ),
    )
    for index, command in enumerate(resource_commands):
        result = _docker(list(command), timeout=30, max_output=32 * 1024)
        if result.returncode != 0:
            continue
        identifiers = [line.strip() for line in result.output.splitlines() if line.strip()]
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
