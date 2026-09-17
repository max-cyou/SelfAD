#!/bin/sh

# Create a timestamped archive of the active SelfAD data volume.
set -eu

if [ "$#" -ne 1 ]; then
    printf '%s\n' "Usage: $0 <existing-backup-directory>" >&2
    exit 64
fi

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=$1
cd "$project_dir"

container_id=$(docker compose ps -q selfad)
if [ -z "$container_id" ]; then
    printf '%s\n' "SelfAD is not running; cannot identify its data volume." >&2
    exit 1
fi
volume_name=$(docker inspect "$container_id" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
if [ -z "$volume_name" ]; then
    printf '%s\n' "Could not find the SelfAD data volume." >&2
    exit 1
fi

exec "$project_dir/scripts/backup-volume.sh" "$volume_name" "$backup_dir"
