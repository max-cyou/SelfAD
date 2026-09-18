#!/bin/sh

# Rebuild the control plane and restart the local Compose installation.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_dir"

docker compose pull runner
docker compose up -d --build --remove-orphans
exec "$project_dir/scripts/ops/doctor.sh"
