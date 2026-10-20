#!/bin/sh
# Disposable bounded live checks on the configured dedicated runner.
set -eu
runner_python=${SELFAD_PYTHON:-/opt/selfad/venv/bin/python}
if [ ! -x "$runner_python" ]; then runner_python=python3; fi
exec "$runner_python" -m selfad.runtime.security_check "$@"
