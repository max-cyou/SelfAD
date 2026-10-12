from selfad.runtime.docker import (
    _docker,
    _label_arguments,
)
from selfad.runtime.types import (
    JURY_IMAGE,
    MAX_COMMAND_OUTPUT,
    RUNNER_LABEL,
    RUNNER_USER,
    CommandResult,
    RunnerError,
)


def _run_jury_script(
    *,
    container_name: str,
    network_name: str,
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
            *_label_arguments(RUNNER_LABEL),
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
            *_label_arguments(RUNNER_LABEL),
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
