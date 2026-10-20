import tempfile
import uuid
from pathlib import Path

from selfad.gitea import download_repository_archive
from selfad.runtime.archive import (
    _extract_repository_archive,
)
from selfad.runtime.cache import (
    _prune_service_image_cache,
    _service_cache_image_name,
)
from selfad.runtime.docker import (
    _docker,
    _docker_quiet,
    _label_arguments,
    runner_is_available,
)
from selfad.runtime.output import (
    _format_log,
    _join_logs,
    _output_tokens,
    _script_failure_message,
    _stdout_has_noise,
)
from selfad.runtime.requirements import (
    _prepare_runtime_image,
    _read_requirements_file,
)
from selfad.runtime.sandbox import (
    _run_jury_script,
    _wait_for_healthcheck,
)
from selfad.runtime.types import (
    _SERVICE_CACHE_BUILD_LOCK,
    RUNNER_LABEL,
    SERVICE_CACHE_LABEL,
    WORK_DIR,
    RunnerError,
    RunnerInfrastructureError,
    RuntimeCheckResult,
)
from selfad.service_contract import ServiceContractResult
from selfad.settings import (
    GiteaSettings,
)


def run_service_runtime_check(
    settings: GiteaSettings,
    *,
    repository_path: str,
    jury_repository_path: str,
    contract: ServiceContractResult,
    exploit_repository_path: str | None = None,
    exploit_commit: str | None = None,
    exploit_runtime_requirements: bytes | None = None,
    cache_service_image: bool = False,
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
        raise RunnerInfrastructureError("The configured Docker runner is unavailable.")

    service_image_cached = False
    service_cache_lock_held = False
    if cache_service_image:
        image_name = _service_cache_image_name(contract.source_commit)
        service_image_cached = _docker(["image", "inspect", image_name], timeout=15).returncode == 0
    else:
        image_name = ""
    source_archive = None
    if not service_image_cached:
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
    if not cache_service_image:
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
        if source_archive is not None:
            _extract_repository_archive(source_archive, source_path)
        _extract_repository_archive(jury_archive, jury_path)
        _extract_repository_archive(exploit_archive, exploit_path)

        try:
            functionality_passed = False
            if cache_service_image and not service_image_cached:
                _SERVICE_CACHE_BUILD_LOCK.acquire()
                service_cache_lock_held = True
                service_image_cached = (
                    _docker(["image", "inspect", image_name], timeout=15).returncode == 0
                )
            if not service_image_cached:
                build_labels = (SERVICE_CACHE_LABEL,) if cache_service_image else (RUNNER_LABEL,)
                build = _docker(
                    [
                        "build",
                        *_label_arguments(*build_labels),
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
                if cache_service_image:
                    _prune_service_image_cache()
                    _SERVICE_CACHE_BUILD_LOCK.release()
                    service_cache_lock_held = False

            jury_requirements = _read_requirements_file(
                jury_path / "requirements.txt",
                label="Jury requirements.txt",
            )
            jury_image, jury_build = _prepare_runtime_image(
                jury_requirements,
                jury_image_name,
                job_path / "jury-runtime",
                jury_path,
                cache_namespace=f"jury:{jury_repository_path}",
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
                    exploit_path,
                    cache_namespace=f"attack:{exploit_repository_path}",
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
                    *_label_arguments(RUNNER_LABEL),
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
                    *_label_arguments(RUNNER_LABEL),
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
                script_name="inject.py",
                target=target,
                image=jury_image,
            )
            if injector.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    False,
                    _script_failure_message("Jury injector", injector.output),
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
                script_name="exploit.py",
                target=target,
                image=exploit_image,
            )
            if exploit.returncode != 0:
                return RuntimeCheckResult(
                    False,
                    True,
                    _script_failure_message("Exploit script", exploit.output),
                    0,
                    _join_logs(
                        ("checker.py", checker_output),
                        ("inject.py", injector.output),
                        ("exploit.py", exploit.output),
                    ),
                )

            recovered_flags = _output_tokens(exploit.stdout)
            stdout_noise = _stdout_has_noise(exploit.stdout)
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
                stdout_noise,
            )
        except RunnerInfrastructureError:
            raise
        except RunnerError as error:
            return RuntimeCheckResult(
                False,
                functionality_passed,
                str(error),
                0,
                _format_log("build", build_log),
            )
        finally:
            if service_cache_lock_held:
                _SERVICE_CACHE_BUILD_LOCK.release()
            for container_name in (
                exploit_name,
                injector_name,
                f"selfad-checker-{job_id}",
                service_name,
            ):
                _docker_quiet(["rm", "--force", container_name])
            _docker_quiet(["network", "rm", network_name])
            if not cache_service_image:
                _docker_quiet(["image", "rm", "--force", image_name])
            _docker_quiet(["image", "rm", "--force", jury_image_name])
            _docker_quiet(["image", "rm", "--force", exploit_image_name])
