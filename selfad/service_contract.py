import ast
from dataclasses import dataclass

import yaml

from selfad.gitea import (
    GiteaFileTooLarge,
    get_branch_commit,
    get_repository,
    get_repository_file,
)
from selfad.settings import GiteaSettings

MAX_CONFIG_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ServiceContractResult:
    valid: bool
    message: str
    source_commit: str | None = None
    jury_commit: str | None = None
    container_port: int | None = None
    healthcheck_path: str | None = None


def validate_service_contract(
    settings: GiteaSettings,
    *,
    repository_path: str,
    jury_repository_path: str,
    default_branch: str,
    source_commit: str | None = None,
    jury_commit: str | None = None,
) -> ServiceContractResult:
    service_repository = get_repository(settings, repository_path)
    jury_repository = get_repository(settings, jury_repository_path)
    if service_repository.empty or jury_repository.empty:
        return _failed("Push files to both repositories before validation.")

    if source_commit is None:
        source_commit = get_branch_commit(
            settings,
            repository_path,
            branch=default_branch,
        )
    if jury_commit is None:
        jury_commit = get_branch_commit(
            settings,
            jury_repository_path,
            branch=default_branch,
        )

    try:
        dockerfile = get_repository_file(
            settings,
            repository_path,
            "Dockerfile",
            ref=source_commit,
            max_bytes=MAX_SOURCE_BYTES,
        )
        config_file = get_repository_file(
            settings,
            repository_path,
            "selfad.yml",
            ref=source_commit,
            max_bytes=MAX_CONFIG_BYTES,
        )
        injector = get_repository_file(
            settings,
            jury_repository_path,
            "inject.py",
            ref=jury_commit,
            max_bytes=MAX_SOURCE_BYTES,
        )
        exploit = get_repository_file(
            settings,
            jury_repository_path,
            "exploit.py",
            ref=jury_commit,
            max_bytes=MAX_SOURCE_BYTES,
        )
        checker = get_repository_file(
            settings,
            jury_repository_path,
            "checker.py",
            ref=jury_commit,
            max_bytes=MAX_SOURCE_BYTES,
        )
        requirements = get_repository_file(
            settings,
            jury_repository_path,
            "requirements.txt",
            ref=jury_commit,
            max_bytes=MAX_CONFIG_BYTES,
        )
    except GiteaFileTooLarge as error:
        return _failed(str(error), source_commit, jury_commit)

    missing_files = [
        name
        for name, content in (
            ("service/Dockerfile", dockerfile),
            ("service/selfad.yml", config_file),
            ("jury/inject.py", injector),
            ("jury/exploit.py", exploit),
        )
        if content is None
    ]
    if missing_files:
        return _failed(
            f"Missing required files: {', '.join(missing_files)}.",
            source_commit,
            jury_commit,
        )

    errors: list[str] = []
    _validate_dockerfile(dockerfile, errors)
    _validate_python_script(injector, "jury/inject.py", errors)
    _validate_python_script(exploit, "jury/exploit.py", errors)
    if checker is not None:
        _validate_python_script(checker, "jury/checker.py", errors)
    if requirements is not None:
        _decode_text(requirements, "jury/requirements.txt", errors)
    container_port, healthcheck_path = _validate_config(config_file, errors)
    if errors:
        return _failed(" ".join(errors), source_commit, jury_commit)

    return ServiceContractResult(
        valid=True,
        message=(
            f"Contract valid on {default_branch}: port {container_port}, "
            f"healthcheck {healthcheck_path}."
        ),
        source_commit=source_commit,
        jury_commit=jury_commit,
        container_port=container_port,
        healthcheck_path=healthcheck_path,
    )


def _failed(
    message: str,
    source_commit: str | None = None,
    jury_commit: str | None = None,
) -> ServiceContractResult:
    return ServiceContractResult(
        valid=False,
        message=message,
        source_commit=source_commit,
        jury_commit=jury_commit,
    )


def _decode_text(content: bytes, name: str, errors: list[str]) -> str | None:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(f"{name} must be UTF-8 text.")
        return None
    if "\x00" in text:
        errors.append(f"{name} must not contain null bytes.")
        return None
    return text


def _validate_dockerfile(content: bytes, errors: list[str]) -> None:
    text = _decode_text(content, "service/Dockerfile", errors)
    if text is None:
        return
    instructions = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not any(line.upper().startswith("FROM ") for line in instructions):
        errors.append("service/Dockerfile must contain a FROM instruction.")


def _validate_python_script(
    content: bytes,
    name: str,
    errors: list[str],
) -> None:
    text = _decode_text(content, name, errors)
    if text is None:
        return
    if not text.strip():
        errors.append(f"{name} must not be empty.")
        return
    try:
        ast.parse(text, filename=name)
    except SyntaxError as error:
        location = f" line {error.lineno}" if error.lineno else ""
        errors.append(f"{name} has invalid Python syntax{location}.")


def _validate_config(
    content: bytes,
    errors: list[str],
) -> tuple[int | None, str | None]:
    text = _decode_text(content, "service/selfad.yml", errors)
    if text is None:
        return None, None
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError:
        errors.append("service/selfad.yml contains invalid YAML.")
        return None, None

    if not isinstance(config, dict):
        errors.append("service/selfad.yml must be a mapping.")
        return None, None
    if config.get("version") != 1 or isinstance(config.get("version"), bool):
        errors.append("service/selfad.yml version must be 1.")

    service = config.get("service")
    if not isinstance(service, dict):
        errors.append("service/selfad.yml must contain a service mapping.")
        return None, None

    port = service.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        errors.append("service.port must be an integer between 1 and 65535.")
        port = None

    healthcheck = service.get("healthcheck")
    if (
        not isinstance(healthcheck, str)
        or not healthcheck.startswith("/")
        or len(healthcheck) > 512
        or any(character.isspace() for character in healthcheck)
    ):
        errors.append("service.healthcheck must be a path beginning with '/'.")
        healthcheck = None

    return port, healthcheck
