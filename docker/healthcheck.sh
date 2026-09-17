#!/bin/sh

set -eu

# Docker's HEALTHCHECK is a liveness probe for this control-plane container.
# Runner availability is a separate readiness concern reported by /ready.
curl --fail --silent http://127.0.0.1:8929/api/healthz >/dev/null \
    && curl --fail --silent http://127.0.0.1:8000/health >/dev/null
