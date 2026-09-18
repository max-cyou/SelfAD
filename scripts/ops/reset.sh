#!/bin/sh

# Delete every piece of SelfAD data (accounts, services, the Gitea instance
# and its repositories) and leave the machine ready for a fresh install.
# The runner TLS certificates volume is kept: it stays valid across resets.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_dir"

confirmed=""
if [ "${1:-}" = "--yes" ]; then
    confirmed=1
    shift
fi
if [ "$#" -gt 0 ]; then
    printf '%s\n' "Usage: $0 [--yes]" >&2
    exit 64
fi

volume_name=selfad_selfad-data
if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
    printf '%s\n' "No SelfAD data volume found; nothing to delete."
    exit 0
fi

if [ -z "$confirmed" ]; then
    printf '%s\n' "This deletes ALL SelfAD data in volume $volume_name:"
    printf '%s\n' "accounts, services, scores and every Gitea repository."
    printf '%s\n' "Create a backup first with ./scripts/ops/backup.sh if in doubt."
    printf '%s' "Type 'delete' to confirm: "
    read -r answer
    [ "$answer" = "delete" ] || {
        printf '%s\n' "Aborted; nothing was deleted."
        exit 1
    }
fi

docker compose down >/dev/null 2>&1 || true
docker volume rm "$volume_name" >/dev/null
printf '%s\n' "Deleted $volume_name. The runner certificates were kept."
printf '%s\n' "Start over with ./scripts/ops/install.sh and complete /setup again."
