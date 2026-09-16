#!/bin/sh

set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <docker-volume> <existing-backup-directory>" >&2
    exit 64
fi

volume_name="$1"
backup_directory="$2"

if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
    echo "Docker volume does not exist: $volume_name" >&2
    exit 66
fi
if [ ! -d "$backup_directory" ]; then
    echo "Backup directory does not exist: $backup_directory" >&2
    exit 66
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
archive_name="selfad-${timestamp}.tar.gz"

docker run --rm \
    --read-only \
    --mount "type=volume,src=${volume_name},dst=/source,readonly" \
    --mount "type=bind,src=${backup_directory},dst=/backup" \
    alpine:3.21 \
    tar -C /source -czf "/backup/${archive_name}" .

printf '%s\n' "Created ${backup_directory%/}/${archive_name}"
