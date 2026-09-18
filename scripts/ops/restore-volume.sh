#!/bin/sh

set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <backup-tar.gz> <new-docker-volume>" >&2
    exit 64
fi

archive=$1
volume_name=$2

if [ ! -f "$archive" ]; then
    echo "Backup archive does not exist: $archive" >&2
    exit 66
fi

if docker volume inspect "$volume_name" >/dev/null 2>&1; then
    echo "Refusing to overwrite existing Docker volume: $volume_name" >&2
    exit 73
fi

docker volume create "$volume_name" >/dev/null

if ! docker run --rm \
    -v "$volume_name:/data" \
    -v "$(dirname "$archive"):/backup:ro" \
    alpine:3.21 \
    tar -xzf "/backup/$(basename "$archive")" -C /data; then
    docker volume rm "$volume_name" >/dev/null 2>&1 || true
    echo "Restore failed; the incomplete target volume was removed." >&2
    exit 1
fi

echo "Restored $archive into the new Docker volume $volume_name"
