import hashlib
import shutil
from pathlib import Path

from selfad.runtime.docker import (
    _docker,
    _label_arguments,
)
from selfad.runtime.types import (
    JURY_IMAGE,
    MAX_REQUIREMENTS_BYTES,
    PARTICIPANT_REQUIREMENT_PATTERN,
    RUNNER_LABEL,
    CommandResult,
    RunnerError,
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
        if requirement and not PARTICIPANT_REQUIREMENT_PATTERN.fullmatch(requirement):
            raise RunnerError(
                "Participant requirements.txt only accepts pinned package==version lines."
            )


def _prepare_runtime_image(
    requirements: bytes | None,
    image_name: str,
    context: Path,
    scripts_path: Path,
    *,
    cache_namespace: str,
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

    if requirements and requirements.strip():
        _validate_requirements(requirements, label="Requirements")
    context.mkdir()
    shutil.copytree(scripts_path, context / "workspace")
    dockerfile = "# syntax=docker/dockerfile:1\nFROM python:3.13-alpine\n"
    if requirements and requirements.strip():
        (context / "requirements.txt").write_bytes(requirements)
        cache_id = hashlib.sha256(
            cache_namespace.encode("utf-8") + b"\0" + requirements
        ).hexdigest()[:16]
        dockerfile += (
            "COPY requirements.txt /tmp/selfad-requirements.txt\n"
            f"RUN --mount=type=cache,id=selfad-pip-{cache_id},"
            "target=/root/.cache/pip,sharing=locked "
            "python -m pip install --disable-pip-version-check "
            "-r /tmp/selfad-requirements.txt "
            "&& rm /tmp/selfad-requirements.txt\n"
        )
    (context / "Dockerfile").write_text(
        dockerfile + "COPY workspace /workspace\n",
        encoding="utf-8",
    )
    build = _docker(
        [
            "build",
            *_label_arguments(RUNNER_LABEL),
            "--tag",
            image_name,
            str(context),
        ],
        timeout=300,
        max_output=2 * 1024 * 1024,
    )
    return image_name, build
