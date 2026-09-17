def organizer_readme(service_name: str, default_branch: str) -> bytes:
    return f"""# {service_name}

This repository contains the vulnerable service. SelfAD builds it as one Docker image and starts one temporary container for every check.

## Required files

```text
Dockerfile
selfad.yml
<service source files>
```

Minimal `selfad.yml`:

```yaml
version: 1
service:
  port: 8000
  healthcheck: /health
```

`port` may be any integer from 1 to 65535. The application must listen on `0.0.0.0` at that exact port. `EXPOSE` in the Dockerfile is optional and does not configure SelfAD.

`healthcheck` must be an HTTP path beginning with `/`, without spaces. SelfAD sends a `GET` request until it receives status 200–399 or the 45-second startup limit expires.

## Runtime limits

- 256 MB RAM, 1 CPU and 128 processes;
- read-only root filesystem;
- writable temporary directories: `/tmp` (64 MB) and `/run` (16 MB);
- no volumes and no persistent data between checks;
- no access to the host or public internet;
- no SelfAD environment variables are passed into the service.

The complete repository is limited to 2,000 files and 64 MB after extraction. `Dockerfile` is limited to 256 KB and `selfad.yml` to 64 KB.

## Jury repository

SelfAD also creates a private `{service_name}` jury repository. Add:

- `inject.py` — inserts test flags;
- `exploit.py` — demonstrates the vulnerability and recovers them;
- `checker.py` — optional legitimate-functionality check;
- `requirements.txt` — optional Python dependencies for jury scripts.

The service can be activated only after the repository contract is valid, the image builds, the healthcheck responds, the functionality checker passes, and the jury exploit recovers at least one injected flag.

Push changes to `{default_branch}`. SelfAD receives the webhook and runs validation automatically. Detailed script examples and error behavior are in the jury repository README.

## Common failures

- `Service image build failed` — the Dockerfile did not build; inspect the build log.
- `Service container failed to start` — `CMD`/`ENTRYPOINT` exited or the container could not start under the limits.
- `Service healthcheck failed` — wrong port/path, listening only on localhost, startup took over 45 seconds, or the endpoint did not return 2xx/3xx.
- `Functionality checker failed` — `checker.py` raised an exception or exited non-zero.
- `Jury injector produced no flags` — stdout contained no exact 32-character `A-Z0-9` line.
- `Runtime check failed` — the jury exploit could not recover an injected flag.
""".encode()


def jury_readme(service_name: str, default_branch: str) -> bytes:
    return f"""# {service_name} — jury

Private scripts used to validate the service and participant defenses. Never put secrets or intended solutions in the public service repository.

## Execution contract

Every script runs as:

```text
python <script>.py http://target:<service-port>
```

The same URL is available in `SELFAD_TARGET`:

```python
import os
import sys

target = (os.environ.get("SELFAD_TARGET") or sys.argv[1]).rstrip("/")
```

The runtime is Python 3.13 on Alpine. The standard library is always available, and local Python modules committed to this repository may be imported.

For third-party packages, add an optional `requirements.txt` to the repository root:

```text
requests==2.32.5
beautifulsoup4==4.14.3
```

SelfAD builds a separate jury image and runs `pip install -r requirements.txt` before starting the check. The file must be UTF-8 and no larger than 64 KB. Use versions compatible with Python 3.13 and Alpine Linux; packages requiring unavailable system build tools may fail to install. The participant attack runtime does not inherit jury dependencies.

Each script has 128 MB RAM, 0.5 CPU, at most 64 processes, a read-only filesystem, a 16 MB writable `/tmp`, no public internet, and a 45-second timeout. stdout and stderr are combined and stored in the check log; total output is limited to 128 KB.

## `inject.py` — required

Create one or more flags, submit them through the normal service interface, then print every successfully inserted flag on its own line. SelfAD does not provide a flag in an environment variable: the injector creates it.

A recognized flag is exactly 32 characters from `A-Z` and `0-9`. Duplicate lines count once. Logging is allowed, but only exact flag lines are scored.

```python
import json
import os
import secrets
import string
import sys
import urllib.request

target = (os.environ.get("SELFAD_TARGET") or sys.argv[1]).rstrip("/")
alphabet = string.ascii_uppercase + string.digits
flag = "".join(secrets.choice(alphabet) for _ in range(32))

# Replace the endpoint and payload with the legitimate service API.
request = urllib.request.Request(
    f"{{target}}/api/flags",
    data=json.dumps({{"flag": flag}}).encode(),
    headers={{"Content-Type": "application/json"}},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    if not 200 <= response.status < 400:
        raise RuntimeError(f"flag insert returned HTTP {{response.status}}")

print(flag)
```

Exit code must be `0`, and at least one valid flag must be printed. If insertion fails, raise an exception or exit non-zero instead of printing a flag that was not stored.

## `exploit.py` — required

Use the intended vulnerability to recover injected flags. Print each recovered flag on its own line using the same exact format.

```python
import os
import sys
import urllib.request

target = (os.environ.get("SELFAD_TARGET") or sys.argv[1]).rstrip("/")

# Replace this with the real exploit and print only recovered flags.
with urllib.request.urlopen(f"{{target}}/vulnerable-endpoint", timeout=5) as response:
    body = response.read().decode()

for line in body.splitlines():
    candidate = line.strip()
    if len(candidate) == 32 and candidate.isascii() and candidate.isalnum() and candidate.upper() == candidate:
        print(candidate)
```

The canonical runtime check passes when the exploit exits `0` and recovers at least one flag printed by `inject.py`.

## `checker.py` — optional

Test legitimate behavior before flags are injected. Create normal data, read it back, and verify response codes and contents. Exit `0` on success. To report a functionality violation, print a short reason to stderr and raise an exception or call `sys.exit(1)`.

```python
import json
import os
import sys
import urllib.request

target = (os.environ.get("SELFAD_TARGET") or sys.argv[1]).rstrip("/")

try:
    request = urllib.request.Request(
        f"{{target}}/api/items",
        data=json.dumps({{"value": "selfad-check"}}).encode(),
        headers={{"Content-Type": "application/json"}},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if not 200 <= response.status < 400:
            raise RuntimeError(f"create returned HTTP {{response.status}}")
except Exception as error:
    print(f"legitimate item flow is broken: {{error}}", file=sys.stderr)
    raise
```

A non-zero checker exit marks participant defense as `Functionality violation` and awards 0 defense points. Its last non-empty output line is shown in the error message, so keep it useful and do not expose secrets.

## Check order and scoring

1. Build and start the service.
2. Wait for the HTTP healthcheck.
3. Run `checker.py`, if present.
4. Run `inject.py` and collect valid flag lines.
5. Run an exploit and intersect its valid flag lines with the injected set.

For the canonical check, the jury exploit is used. For an attack check, the participant exploit is used. For a defense check, the participant service is built and the jury scripts are used.

The organizer configures attack and defense formulas in SelfAD. Attack may use recovered-flag coverage or points per flag. Defense may use protected-flag coverage or points lost per leaked flag. A submission that does not improve the raw score can add a fixed or percentage penalty to the next improved result. The best awarded score is retained. Recovering at least one flag unlocks defense.

Push to `{default_branch}` to run validation automatically.

## Common failures

- `Jury injector failed` / `Jury exploit failed` — the script exited non-zero; read its traceback in the log.
- `produced no flags on stdout` — no output line matched `^[A-Z0-9]{{32}}$`.
- `Command timed out after 45 seconds` — the script did not finish in time.
- `Command output exceeded 131072 bytes` — reduce debug output.
- `Functionality checker failed: ...` — the checker raised or exited non-zero; the final output line becomes the detail.
- `Jury dependency installation failed` — `requirements.txt` contains an invalid, unavailable, or Alpine-incompatible dependency; inspect the pip build log.
""".encode()


def attack_readme(service_name: str, default_branch: str) -> bytes:
    return f"""# {service_name} — attack

Implement the exploit in `exploit.py`. Do not change or delete `Dockerfile`; SelfAD rejects the push check if its exact contents change.

## Runtime

Your script runs with Python 3.13 on Alpine as:

```text
python exploit.py http://target:<service-port>
```

The target is also available as `SELFAD_TARGET`:

```python
import os
import sys

target = (os.environ.get("SELFAD_TARGET") or sys.argv[1]).rstrip("/")
```

Only the service is reachable at runtime. The organizer may provide fixed Python packages in the attack runtime. If the organizer explicitly enables it, you may also add a UTF-8 `requirements.txt` (up to 64 KB) to this repository; its packages are installed in addition to the fixed list. Participant requirements accept only pinned PyPI package lines such as `requests==2.32.5` (including optional extras); URLs, git dependencies, pip options and version ranges are rejected. Otherwise a non-empty participant `requirements.txt` is rejected. The runner allows 128 MB RAM, 0.5 CPU, 64 processes, a read-only filesystem, a 16 MB `/tmp`, 45 seconds, and 128 KB of combined stdout/stderr.

## Output and score

Print every recovered flag on its own line. A valid flag is exactly 32 characters containing only uppercase `A-Z` and digits `0-9`:

```python
print("A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6")
```

Only flags that were printed by the jury injector in the same run count. Duplicates count once. The organizer may ignore non-flag stdout, treat it as an unsuccessful submission, or apply an additional percentage cost; send diagnostics to stderr and keep stdout flag-only.

The organizer chooses either percentage coverage or points per matched flag. Your best awarded attack score is retained, and at least one match unlocks the defense repository. A submission that does not improve your raw result can add penalty debt to the next improvement. Exit code must be `0`; an exception, timeout, or non-zero exit gives no result for that run and may be penalized.

Push to `{default_branch}` to start the check automatically.

## Common failures

- `Dockerfile changes are not allowed` — restore the original Dockerfile byte-for-byte.
- `Attack repository must contain a non-empty exploit.py` — restore or implement the file.
- `Jury exploit failed` — your `exploit.py` exited non-zero; the name is generic in the current runner log.
- `the exploit recovered no injected flags` — output format is wrong or the exploit did not recover current-run flags.
- timeout/output-limit errors — finish within 45 seconds and keep output below 128 KB.
""".encode()


def defense_readme(
    service_name: str,
    default_branch: str,
    original_readme: bytes | None = None,
) -> bytes:
    readme = f"""# {service_name} — defense

This repository is a copy of the vulnerable service. It unlocks after your attack recovers at least one jury flag.

Fix the vulnerabilities without breaking legitimate behavior. You may edit source files and `selfad.yml`, add files, and change application logic. Do not change or delete `Dockerfile`; SelfAD compares it byte-for-byte with the original.

## What SelfAD runs

1. Validate `Dockerfile` and `selfad.yml`.
2. Build your service image.
3. Start it under the service limits and wait for the configured healthcheck.
4. Run the optional jury `checker.py` against legitimate behavior.
5. Run the jury `inject.py` to store fresh flags.
6. Run the jury `exploit.py` against your patched service.

The port in `selfad.yml` must match the port on which the service listens on `0.0.0.0`. The healthcheck path must continue returning HTTP 200–399 within 45 seconds.

The service runs with 256 MB RAM, 1 CPU, 128 processes, a read-only root filesystem, writable `/tmp` and `/run`, no volumes, no persistence between checks, and no public internet.

## Score

- the configured defense maximum when the functionality checker passes and the jury exploit recovers no flags;
- a partial result based on either protected-flag percentage or configured points lost per leaked flag;
- no raw points on build, startup, healthcheck, contract, injector, or functionality failure.

A submission that does not improve the raw result can add fixed-point or percentage penalty debt to the next improvement. The best awarded defense score is retained.

Push to `{default_branch}` after defense is unlocked. Every push to that branch starts a new check.

## Common failures

- `Defense unlocks after a successful attack` — first recover at least one flag in the attack repository.
- `Dockerfile changes are not allowed` — restore it byte-for-byte; make the fix in source/configuration instead.
- `Service image build failed` — inspect the build log.
- `Service healthcheck failed` — verify the configured port/path, bind address, startup time, and HTTP status.
- `Functionality violation` — the service starts, but the jury's legitimate-data flow is broken. The checker detail is included in the message.
- `Runtime check passed: recovered ...` — the patch is incomplete; the jury exploit still reads current-run flags.

Do not hardcode or filter the example flag value: flags are generated by the jury injector and may differ on every run.
""".encode()
    if original_readme:
        readme += b"\n---\n\n## Original service README\n\n" + original_readme
    return readme
