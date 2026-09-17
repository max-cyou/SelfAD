#!/bin/sh

# Run inside a SelfAD control-plane container after a runner is configured.
# This is intentionally small: it verifies the effective Docker constraints
# without building another service image or contacting the public network.
set -eu

export DOCKER_HOST="${SELFAD_RUNNER_DOCKER_HOST:?Configure a runner first}"
export DOCKER_TLS_VERIFY=1
export DOCKER_CERT_PATH="${SELFAD_RUNNER_CERT_PATH:?Configure runner mTLS first}"

network="selfad-isolation-smoke-$$"
container="selfad-isolation-limits-$$"

cleanup() {
    docker rm -f "$container" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal --label selfad.managed=true "$network" >/dev/null
docker create \
    --name "$container" \
    --label selfad.managed=true \
    --network "$network" \
    --memory 256m \
    --memory-swap 256m \
    --cpus 1 \
    --pids-limit 128 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    python:3.13-alpine sleep 20 >/dev/null

docker inspect --format \
    'limits memory={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} pids={{.HostConfig.PidsLimit}} readonly={{.HostConfig.ReadonlyRootfs}} capdrop={{json .HostConfig.CapDrop}}' \
    "$container"

docker run --rm \
    --network "$network" \
    --memory 64m \
    --memory-swap 64m \
    --cpus 0.25 \
    --pids-limit 32 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=8m \
    --user 10001:10001 \
    python:3.13-alpine \
    python -c 'import socket,sys; socket.setdefaulttimeout(3); sock=socket.socket(); result=sock.connect_ex(("1.1.1.1", 443)); print("egress_blocked" if result else "egress_open"); sys.exit(0 if result else 1)'
