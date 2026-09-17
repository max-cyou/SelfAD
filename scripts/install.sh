#!/bin/sh

# Start a local SelfAD instance with its isolated Docker runner.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file="$project_dir/.env"
host=""

usage() {
    printf '%s\n' "Usage: $0 [--host <LAN-IP-or-DNS-name>]"
    printf '%s\n' "Example: $0 --host 192.168.1.154"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            host=$2
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 64
            ;;
    esac
done

command -v docker >/dev/null 2>&1 || {
    printf '%s\n' "Docker Engine is required. Install Docker, then run this script again." >&2
    exit 69
}
docker compose version >/dev/null 2>&1 || {
    printf '%s\n' "Docker Compose v2 is required (the 'docker compose' command)." >&2
    exit 69
}
docker info >/dev/null 2>&1 || {
    printf '%s\n' "Docker is not running or this user cannot access it." >&2
    exit 69
}

if [ ! -f "$env_file" ]; then
    cp "$project_dir/.env.example" "$env_file"
    if [ -n "$host" ]; then
        case "$host" in
            *[!A-Za-z0-9.-]*|'')
                printf '%s\n' "Host must be a DNS name or IP address." >&2
                exit 64
                ;;
        esac
        sed -i \
            -e "s|^SELFAD_GITEA_PUBLIC_URL=.*|SELFAD_GITEA_PUBLIC_URL=http://$host:8929|" \
            -e "s|^SELFAD_GITEA_DOMAIN=.*|SELFAD_GITEA_DOMAIN=$host|" \
            -e "s|^SELFAD_GITEA_SSH_DOMAIN=.*|SELFAD_GITEA_SSH_DOMAIN=$host|" \
            "$env_file"
    fi
    printf '%s\n' "Created .env. Clone links will use ${host:-localhost}."
elif [ -n "$host" ]; then
    printf '%s\n' ".env already exists; its configured host was left unchanged." >&2
fi

cd "$project_dir"
docker compose config -q
docker compose up -d --build

container_id=$(docker compose ps -q selfad)
for attempt in $(seq 1 45); do
    health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)
    [ "$health" = "healthy" ] && break
    sleep 2
done

if [ "${health:-}" != "healthy" ]; then
    printf '%s\n' "SelfAD did not become healthy. Run ./scripts/doctor.sh for details." >&2
    exit 1
fi

http_port=$(docker compose port selfad 8000 | sed 's/.*://')
gitea_port=$(docker compose port selfad 8929 | sed 's/.*://')
printf '%s\n' "SelfAD is ready: http://localhost:$http_port"
printf '%s\n' "Gitea is ready:  http://localhost:$gitea_port"
printf '%s\n' "Open SelfAD and complete the first setup form."
