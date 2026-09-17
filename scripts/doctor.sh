#!/bin/sh

# Show the state of the local Compose installation and run the readiness check.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"

command -v docker >/dev/null 2>&1 || {
    printf '%s\n' "Docker Engine is not installed." >&2
    exit 69
}
docker compose version >/dev/null 2>&1 || {
    printf '%s\n' "Docker Compose v2 is not installed." >&2
    exit 69
}

docker compose ps
container_id=$(docker compose ps -q selfad)
if [ -z "$container_id" ]; then
    printf '%s\n' "SelfAD is not running. Start it with ./scripts/install.sh." >&2
    exit 1
fi

health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)
printf '%s\n' "SelfAD health: ${health:-unknown}"
if [ "$health" != "healthy" ]; then
    docker compose logs --tail=80 selfad >&2
    exit 1
fi

docker compose exec -T selfad /opt/selfad/healthcheck.sh
printf '%s\n' "SelfAD, Gitea and the isolated runner are ready."
