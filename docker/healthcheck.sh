#!/bin/sh

set -eu

export DOCKER_HOST="${SELFAD_RUNNER_DOCKER_HOST:-unix:///run/selfad-docker/docker.sock}"
if [ "${SELFAD_RUNNER_TLS_VERIFY:-false}" = "true" ] || [ "${SELFAD_RUNNER_TLS_VERIFY:-false}" = "1" ]; then
    export DOCKER_TLS_VERIFY=1
fi
if [ -n "${SELFAD_RUNNER_CERT_PATH:-}" ]; then
    export DOCKER_CERT_PATH="${SELFAD_RUNNER_CERT_PATH}"
fi

curl --fail --silent http://127.0.0.1:8929/api/healthz >/dev/null \
    && curl --fail --silent http://127.0.0.1:8000/health >/dev/null \
    && docker info >/dev/null 2>&1
