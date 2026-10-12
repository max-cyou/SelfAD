from selfad.runtime.docker import (
    _docker,
    _docker_quiet,
    _instance_label,
)
from selfad.runtime.types import (
    MAX_CACHED_SERVICE_IMAGES,
    SERVICE_CACHE_LABEL,
)


def _service_cache_image_name(source_commit: str) -> str:
    return f"selfad-service-cache-{source_commit.lower()}:latest"


def _prune_service_image_cache() -> None:
    result = _docker(
        [
            "images",
            "--format",
            "{{.Repository}}:{{.Tag}}",
            "--filter",
            f"label={SERVICE_CACHE_LABEL}",
            "--filter",
            f"label={_instance_label()}",
        ],
        timeout=30,
        max_output=32 * 1024,
    )
    if result.returncode != 0:
        return
    images = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(images) > MAX_CACHED_SERVICE_IMAGES:
        _docker_quiet(["image", "rm", "--force", *images[MAX_CACHED_SERVICE_IMAGES:]])
