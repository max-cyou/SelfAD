#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <selfad-base-url>" >&2
    echo "Example: $0 https://ctf.example" >&2
    exit 64
fi

base_url=${1%/}
response_file=$(mktemp)
headers_file=$(mktemp)
trap 'rm -f "$response_file" "$headers_file"' EXIT HUP INT TERM

if ! curl --fail --silent --show-error \
    --dump-header "$headers_file" \
    "$base_url/ready" >"$response_file"; then
    echo "Preflight failed: $base_url/ready is not healthy." >&2
    exit 1
fi

for expected in '"status":"ok"' '"database":true' '"gitea":true' '"runner":true'; do
    if ! grep -F "$expected" "$response_file" >/dev/null; then
        echo "Preflight failed: readiness response lacks $expected." >&2
        cat "$response_file" >&2
        exit 1
    fi
done

if ! grep -i '^strict-transport-security:' "$headers_file" >/dev/null; then
    echo "Preflight failed: HTTPS response lacks Strict-Transport-Security." >&2
    exit 1
fi

expected_runner=${SELFAD_PRECHECK_EXPECTED_RUNNER:-external}
if ! grep -F "\"runner_mode\":\"$expected_runner\"" "$response_file" >/dev/null; then
    echo "Preflight failed: runner mode is not $expected_runner." >&2
    cat "$response_file" >&2
    exit 1
fi

if [ -z "${SELFAD_METRICS_TOKEN:-}" ]; then
    echo "Preflight failed: SELFAD_METRICS_TOKEN is required." >&2
    exit 1
fi
if ! curl --fail --silent --show-error \
    -H "Authorization: Bearer $SELFAD_METRICS_TOKEN" \
    "$base_url/metrics" \
    | grep -F 'selfad_runner_mode' >/dev/null; then
    echo "Preflight failed: authenticated metrics check failed." >&2
    exit 1
fi

printf '%s\n' "Preflight passed: $base_url is ready with $expected_runner runner."
