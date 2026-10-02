<div align="center">

# SelfAD

### A self-hosted Attack-Defense CTF platform for building, breaking and patching real services

[![Tests](https://github.com/max-cyou/SelfAD/actions/workflows/tests.yml/badge.svg)](https://github.com/max-cyou/SelfAD/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-22a06b.svg)](LICENSE)
[![Python 3.11 | 3.14](https://img.shields.io/badge/python-3.11%20%7C%203.14-3776ab.svg)](requirements.txt)
[![Docker Compose](https://img.shields.io/badge/deploy-Docker%20Compose-2496ed.svg)](compose.yaml)
[![Gitea 1.27.3](https://img.shields.io/badge/Gitea-1.27.3-609926.svg)](Dockerfile)

SelfAD combines an organiser panel, private Git hosting, isolated Docker checks,
automatic scoring and a live scoreboard in one deployable stack.

[Quick Start](#quick-start) · [How It Works](#how-it-works) · [Deployments](#choose-a-deployment) · [Service Authoring](#service-authoring) · [Operations](#operations) · [Security](#security-model)

</div>

---

## Why SelfAD

SelfAD is designed for self-hosted Attack-Defense events where every
participant receives:

- the vulnerable service source;
- a private **attack repository** for an exploit;
- a private **defense repository** for a patch;
- automatic checks on every push;
- separate attack and defense scores.

The organiser prepares a service and its jury scripts once. SelfAD provisions
private repositories, validates immutable Dockerfiles, processes signed Gitea
webhooks, runs the checks and updates the scoreboard.

### Included

| Area | What SelfAD provides |
|---|---|
| Control plane | FastAPI panel, setup wizard, registration, invite codes, contest schedule |
| Git hosting | Bundled private Gitea, participant SSH keys, repository provisioning |
| Runner | Docker builds, health checks, jury scripts, resource limits, automatic cleanup |
| Competition | Attack/defense repository workflow, configurable scoring and penalties |
| Operations | Install, update, doctor, backup, reset, preflight and smoke-test scripts |
| Deployment | Local Compose, automatic HTTPS overlay and separate-runner production profile |

---

> [!IMPORTANT]
> The bundled Docker-in-Docker runner is intended for development, private
> events and participants you are prepared to trust at the host-risk level.
> Public events should use the separate-runner topology described in
> [Production Deployment](docs/production.md).

## Navigation

- [Quick Start](#quick-start)
- [Choose a Deployment](#choose-a-deployment)
- [Domain and HTTPS](#domain-and-https)
- [How It Works](#how-it-works)
- [Competition Lifecycle](#competition-lifecycle)
- [Service Authoring](#service-authoring)
- [Scoring](#scoring)
- [Security Model](#security-model)
- [Operations](#operations)
- [Monitoring and Event Runbook](#monitoring-and-event-runbook)
- [Capacity Planning](#capacity-planning)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)

## Quick Start

### Requirements

- Docker Engine;
- Docker Compose v2 (`docker compose`);
- enough disk for service images and build cache;
- ports `8000`, `8929` and `2224` available for the default local profile.

### Install

```bash
git clone https://github.com/max-cyou/SelfAD.git
cd SelfAD
./scripts/ops/install.sh
```

Open [http://localhost:8000](http://localhost:8000) and complete the setup
form. The organiser username and password work in both SelfAD and Gitea.

The installer starts:

1. SelfAD and the bundled Gitea instance;
2. an isolated Docker-in-Docker runner with mTLS;
3. persistent Docker volumes;
4. health checks for the panel, Gitea and runner.

Data survives container restarts and `docker compose down`.

### LAN install

Use the LAN address before first start so Gitea displays clone URLs reachable
from other devices:

```bash
./scripts/ops/install.sh --host 192.168.1.154
```

### Verify

```bash
./scripts/ops/doctor.sh
curl http://localhost:8000/ready
```

A ready stack returns:

```json
{"status":"ok","database":true,"gitea":true,"runner":true}
```

## Choose a Deployment

| Profile | Intended use | Start command |
|---|---|---|
| Local / LAN | Development, workshops, trusted private events | `./scripts/ops/install.sh` |
| Existing nginx/Caddy | Domain on a host that already terminates TLS | Base Compose + proxy configuration |
| Bundled HTTPS | Clean host, domain, automatic Let's Encrypt | `docker compose -f compose.yaml -f compose.https.yaml up -d` |
| Separate runner | Public/untrusted event | `docker-compose.production.yml` + dedicated runner VM |

### Local / LAN

The shortest path. SelfAD and Gitea are exposed directly over plain HTTP. Do
not send real participant passwords over an untrusted network in this mode.

### Bundled HTTPS overlay

Point two DNS A/AAAA records at the host before starting:

```text
ctf.example      -> server IP
git.ctf.example  -> server IP
```

Add to `.env`:

```dotenv
SELFAD_PUBLIC_DOMAIN=ctf.example
SELFAD_GITEA_PUBLIC_URL=https://git.ctf.example
SELFAD_GITEA_DOMAIN=git.ctf.example
SELFAD_GITEA_SSH_DOMAIN=git.ctf.example

# Prevent bypassing Caddy over plaintext origin ports
SELFAD_HTTP_BIND=127.0.0.1
SELFAD_GITEA_HTTP_BIND=127.0.0.1
```

Open `80/tcp`, `443/tcp+udp` and `2224/tcp`, then start:

```bash
docker compose -f compose.yaml -f compose.https.yaml up -d
```

Caddy obtains and renews Let's Encrypt certificates automatically.

> [!NOTE]
> `scripts/ops/update.sh` currently operates on the base Compose file. For an
> HTTPS-overlay installation, update with the same two-file Compose command.

### Production topology

For a public event, place the control plane and runner on separate machines or
VMs:

```text
Internet
   |
   v
Caddy / nginx :443
   |
   +--> SelfAD + Gitea + PostgreSQL   (control-plane host)
                  |
                  | Docker API over mTLS/private network
                  v
             disposable runner VM
```

The control plane must not be privileged and must not mount a Docker socket.
See [Production Deployment](docs/production.md) and
[Runner Host](docs/runner-host.md).

## Domain and HTTPS

DNS only maps a hostname to an IP address; it does not map a domain to port
8000. A reverse proxy listens on 80/443 and routes by hostname:

```text
ctf.example       -> 127.0.0.1:8000
git.ctf.example   -> 127.0.0.1:8929
Git SSH           -> server:2224 (not an HTTP proxy route)
```

### SelfAD environment

```dotenv
SELFAD_TRUST_PROXY_HEADERS=true
SELFAD_GITEA_PUBLIC_URL=https://git.ctf.example
SELFAD_GITEA_DOMAIN=git.ctf.example
SELFAD_GITEA_SSH_DOMAIN=git.ctf.example
SELFAD_GITEA_SSH_PORT=2224
SELFAD_HTTP_BIND=127.0.0.1
SELFAD_GITEA_HTTP_BIND=127.0.0.1
```

For nginx/Caddy on the Docker host, the default trusted proxy address is the
pinned Compose gateway `172.31.201.1`. Set `SELFAD_TRUSTED_PROXY_HOSTS`
explicitly only for another topology.

The proxy must forward:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Use at least `client_max_body_size 32m` for the Gitea host.

### Cloudflare

- Proxied mode supports 80/443 and selected alternate ports, not SelfAD's
  default `8000/8929`. Route those origins through nginx/Caddy first.
- Use **Full (strict)** TLS. Flexible mode leaves Cloudflare-to-origin traffic
  unencrypted.
- Automatic HTTPS Rewrites may hide a proxy-trust mistake in common HTML
  attributes while JavaScript `data-*` URLs remain `http://`; fix proxy trust,
  not CSP.
- Cloudflare does not proxy arbitrary Git SSH on the free HTTP proxy. Keep the
  SSH hostname DNS-only or expose port 2224 directly.

Verify the scheme SelfAD sees:

```bash
curl -s https://ctf.example/login | grep -o 'action="[^"]*"'
```

The form action must start with `https://`.

## How It Works

### Push-to-score pipeline

```text
git push
   |
   v
Gitea webhook --HMAC-SHA256--> SelfAD queue
                                      |
                                      v
                                repository worker
                                      |
                     +----------------+----------------+
                     |                                 |
                     v                                 v
              build service                     build jury/attack
                     |                                 |
                     +----------> isolated network <---+
                                      |
                             health -> checker
                                      |
                                  injector
                                      |
                                   exploit
                                      |
                            flags -> score -> UI
```

Every webhook is size-limited, signed, delivery-deduplicated and branch-
checked. The queue accepts at most 25 pending events per repository. Workers
claim batches atomically and process the latest commit.

Submission history is bounded to the last 50 rows per participant/service.
Scoring aggregates, penalty debt and first/last solution timestamps are stored
separately, so pruning does not change results.

### Repository model

The organiser owns:

- a service source repository;
- a private jury repository.

Each participant receives:

- read access to the organiser source;
- a private writable attack repository;
- a private writable defense repository.

Participant Dockerfiles are pinned by SHA-256. A changed Dockerfile is rejected
before the runner executes the submission.

## Competition Lifecycle

```text
not_started  ->  started  ->  ended
```

The organiser may start/end manually or configure UTC-backed scheduled times.
No cron process is required: requests and webhook processing apply due state
transitions.

### Organiser workflow

1. Complete `/setup` and create the organiser account.
2. Create a service in Admin; SelfAD creates source and jury repositories.
3. Push service code and jury scripts.
4. Press **Validate**. Static contract checks and the full runtime check must
   pass.
5. Activate and issue the service to participants.
6. Configure registration, optional invite code, schedule and scoring.
7. Run a participant-path smoke test before announcing the event.

### Participant workflow

1. Register with username, email, password and SSH public key.
2. Clone the source, attack and defense repositories over SSH.
3. Push `exploit.py` to attack. A successful attack unlocks defense.
4. Patch the service in defense and push.
5. Follow status, diagnostic messages and points on `/services`.

Before start, participant service pages and scoreboard remain hidden. After
end, the scoreboard remains visible but registration/submissions close.

## Service Authoring

Every service uses two repositories.

### Source repository

Required:

```text
Dockerfile
selfad.yml
service files...
```

`selfad.yml` schema:

```yaml
version: 1
service:
  port: 8080
  healthcheck: /health
```

Rules:

- `version` must be `1`;
- `service.port` must be an integer from 1 to 65535;
- `service.healthcheck` starts with `/`, has no whitespace and is at most 512
  characters;
- the Dockerfile must contain `FROM`;
- config is limited to 64 KiB, individual source/jury files to 256 KiB;
- repository archives are limited to 2,000 files and 64 MiB extracted;
- text must be UTF-8 without NUL bytes.

The service runtime receives 256 MiB RAM, 1 CPU, 128 PIDs, no capabilities,
`no-new-privileges`, read-only rootfs and noexec tmpfs at `/tmp` and `/run`.

### Jury repository

```text
inject.py          # required
exploit.py         # required
checker.py         # optional, recommended
requirements.txt   # optional, pinned dependencies
```

Jury scripts receive the target URL in both:

```python
import os
import sys

target = os.environ["SELFAD_TARGET"]
assert target == sys.argv[1]
```

They run as uid `10001` with 128 MiB RAM, 0.5 CPU, 64 PIDs, read-only rootfs,
no capabilities and a 45-second wall clock.

#### `checker.py`

Runs first against the pristine service. Exit `0` means functional. Any other
exit code fails the attempt and exposes the last output line in the status.

Use it to distinguish a broken service from a failed exploit.

#### `inject.py`

Plants flags and prints every planted flag to stdout, one per line. Flags must
match:

```regex
^[A-Z0-9]{32}$
```

A non-zero exit or no valid flags fails the attempt.

#### `exploit.py`

Recovers and prints flags, one per line. It must exit `0` within 45 seconds.
Print diagnostics to stderr: non-flag stdout is considered noise and may be
ignored, marked unsuccessful or penalised according to scoring settings.

### Check order

1. Build service image.
2. Start service as network alias `target`.
3. Wait for health endpoint.
4. Run checker (if present).
5. Run injector and collect expected flags.
6. Run exploit and collect recovered flags.
7. Compare sets, score and clean up.

### Common author errors

| Message | Meaning |
|---|---|
| `Service image build failed.` | Dockerfile/build context failed |
| `Service healthcheck failed: ...` | port/path mismatch or startup failure |
| `Functionality checker failed: ...` | service is broken before injection |
| `Jury injector failed: ...` | injector crashed; last line is shown |
| `Jury injector produced no flags on stdout.` | no valid expected flags |
| `Exploit script failed: ...` | exploit crashed; last line is shown |
| `Command timed out after 45 seconds.` | script exceeded wall clock |
| `Command output exceeded ...` | reduce stdout/stderr |
| `Defense check failed: ... X of Y flags.` | patch still leaks flags |
| `Dockerfile does not match the pinned version.` | participant modified immutable Dockerfile |

### Dependencies and caching

Jury and optional participant attack requirements must be pinned. Participant
requirements accept `package==version` lines only; URLs, git dependencies,
options and ranges are rejected.

SelfAD caches validated canonical service images by full source commit. The
cache is limited to 32 images. Generated jury/attack images use namespaced
BuildKit pip caches keyed by repository and requirements content, preventing
unrelated participants from sharing arbitrary package cache state.

Build dependencies still execute code as root during Docker build. Use trusted
packages, hashes and an internal package mirror for public events.

## Scoring

### Attack

- `coverage`: `max_points × matched / injected`;
- `per_flag`: `matched × points_per_flag`, capped at max points.

### Defense

- `coverage`: `max_points × (injected - matched) / injected`;
- per-flag loss: maximum minus configured points for each leaked flag.

### Attempts and penalties

- score is best-of: a bad later push never lowers the stored best score;
- non-improving attempts may accumulate debt charged to the next improvement;
- modes: `none`, `points`, `percent`, `compound_percent`;
- separate free failures for attack and defense;
- check errors may be exempted;
- stdout noise: `ignore`, `unsuccessful`, or `percent_penalty`.

Scoreboard ordering: total score, earliest first solution, earliest last
solution, username. Equal scores share a rank; zero-point users are hidden.

## Security Model

### Implemented controls

- HMAC-SHA256 signed Gitea webhooks with constant-time comparison;
- 1 MiB streaming webhook body limit and delivery deduplication;
- CSRF tokens on state-changing forms;
- signed SameSite session cookies and optional Secure flag;
- token-gated first-run setup in public-event mode;
- scrypt password hashes in SelfAD (authentication is verified by Gitea);
- CSP, frame denial, MIME sniffing protection and restrictive permissions
  policy;
- private repositories and SSH-key participant access;
- immutable participant Dockerfile hashes;
- archive path-traversal/file-count/size protection;
- cap-drop ALL, no-new-privileges, read-only filesystems, noexec tmpfs;
- internal per-job network and CPU/RAM/PID/nofile limits;
- labelled cleanup of managed Docker resources;
- bounded pending queue and submission audit history;
- fail-closed public-event configuration checks and single-process ownership;
- per-installation runner labels that scope crash cleanup;

### Trust boundaries

Participant exploit and defense source code remains untrusted. Containers are
a strong control, not an absolute VM boundary. Build-time package installation
is a separate risk from runtime network isolation.

For public events, use a disposable runner VM reachable only from the control
plane over mTLS. Never expose unauthenticated Docker TCP.

### Login protection

SelfAD applies per-process login and registration limits. Gitea web login uses
the same passwords but has no equivalent per-IP limiter, so rate-limit
`/user/login` at nginx/Caddy/fail2ban. Participants use SSH for Git and do not
need the Gitea web login.

See [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Operations

### Command reference

```bash
./scripts/ops/doctor.sh                  # readiness and container state
./scripts/ops/update.sh                  # rebuild and restart after update
./scripts/ops/backup.sh /srv/backups     # archive active data volume
./scripts/ops/reset.sh                   # interactive factory reset
./scripts/ops/reset.sh --yes             # destructive non-interactive reset
docker compose down                      # stop, preserve data
docker compose up -d                     # start existing installation
```

### Data model

In the bundled profile, `selfad-data` contains the SelfAD database, Gitea
database/repositories, tokens and generated secrets. Treat it as the whole
event state.

> [!WARNING]
> `docker compose down -v` and `scripts/ops/reset.sh` delete event data. Back
> up before destructive maintenance. Do not use broad Docker volume pruning on
> a host with data you have not identified.

### Safe update

1. Back up.
2. Update the entire repository (prefer a real git clone over copied files).
3. Run `./scripts/ops/update.sh`.
4. Wait for healthy containers and `/ready` 200.
5. Verify login, SSH clone and a disposable event-flow smoke test.

### Smoke tests

```bash
# Production readiness
SELFAD_METRICS_TOKEN=... \
  ./scripts/event/event-preflight.sh https://ctf.example

# Copy the disposable Python tests into the running control plane
docker cp scripts/event/runner-smoke.py selfad-selfad-1:/tmp/runner_smoke.py
docker cp scripts/event/event-flow-smoke.py selfad-selfad-1:/tmp/event_flow_smoke.py
docker cp scripts/event/cache-load-smoke.py selfad-selfad-1:/tmp/cache_load_smoke.py

# Real Gitea -> runner canonical/attack/defense path
docker compose exec -T selfad sh -lc \
  'cd /app && PYTHONPATH=/tmp:/app /opt/selfad/venv/bin/python /tmp/runner_smoke.py'

# Signed webhook -> queue -> scoring -> defense unlock
docker compose exec -T selfad sh -lc \
  'cd /app && PYTHONPATH=/tmp:/app /opt/selfad/venv/bin/python /tmp/event_flow_smoke.py'

# Concurrent cached-attack benchmark
docker compose exec -T selfad sh -lc \
  'cd /app && PYTHONPATH=/tmp:/app /opt/selfad/venv/bin/python /tmp/cache_load_smoke.py --concurrency 2 --checks 8'
```

The Python smoke scripts are designed to run inside the control-plane
container, where the private Gitea token and runner mTLS settings are available.
They use unique repositories and remove test resources in `finally`.

## Monitoring and Event Runbook

### Endpoints

| Endpoint | Purpose | Exposure |
|---|---|---|
| `/health` | Process liveness | public is acceptable |
| `/ready` | Database + Gitea + runner readiness | monitor before/during event |
| `/metrics` | Prometheus queue/service/runner signals | Bearer token required |

Monitor:

- pending queue depth and oldest processing age;
- host load average and available RAM;
- free disk and Docker disk usage;
- container restart count and OOMKilled;
- runner container/image/build-cache growth;
- participant-visible check latency.

### Before the event

1. Verify DNS, certificate expiry and HTTP→HTTPS.
2. Confirm ports 8000/8929 are not public; expose only 80/443/2224.
3. Create and restore-test a backup.
4. Require `/ready` 200.
5. Pre-pull required base images.
6. Clone as a test participant using SSH.
7. Complete an attack and a one-line defense through the real push path.
8. Verify registration, invite code, schedule/timezone and scoreboard order.
9. Rate-limit Gitea login at the proxy.

### During the event

- Watch queue depth/age, not only `/ready`.
- Do not increase worker concurrency blindly during a queue incident.
- If the runner wedges, pause submissions, inspect labelled resources,
  restart the runner, then restart SelfAD to return interrupted events to
  PENDING.
- Keep the control plane online even if runner checks are temporarily paused.

### After the event

1. End the contest and drain/record queue state.
2. Export scoreboard/logs and make a final backup.
3. Rebuild a public-event runner VM.
4. Apply the announced repository retention policy.
5. Review common check failures before the next event.

## Capacity Planning

Website visitors are cheap; Docker build-and-check jobs are the bottleneck.
Worker concurrency describes simultaneous full checks, not simultaneous logged
in users.

### Reference benchmark

Measured on a 2 vCPU / 3 GiB RAM VPS with simple Python service images:

| Load | Work | Time | Peak load | Min available RAM | Result |
|---|---:|---:|---:|---:|---|
| 1 cold smoke | 3 checks | 52s | 1.43 | 1432 MiB | healthy |
| Full webhook/scoring flow | 3 checks | 50s | 1.02 | 1421 MiB | healthy |
| 2 concurrent smoke batches | 6 checks | 57s | 1.67 | 1291 MiB | healthy |
| 4 concurrent smoke batches | 12 checks | 85s | 5.97 | 1084 MiB | CPU saturated |

Recommended setting on that host:

```dotenv
SELFAD_WORKER_CONCURRENCY=2
```

Canonical image caching improved a warmed attack check from approximately
14.3s to 13.1s. Docker layer caching already did most of the work, so more CPU
produces a larger gain than additional caching.

### Starting points

| Runner | Start with | Practical expectation |
|---|---:|---|
| 2 vCPU / 3–4 GiB | concurrency 2 | Small events; large push bursts queue |
| 4 vCPU / 4–8 GiB | concurrency 3, test 4 | 50–100 participants with minute-scale burst queues |
| 8 vCPU / 8–16 GiB | concurrency 6, test 8 | About 100 participants with shorter queues |

On 4 vCPU / 4 GiB, raise the runner container limit from 768 MiB to roughly
2 GiB or the additional host memory cannot help runner jobs.

Estimate a burst with:

```text
queue drain time ~= pushes / measured checks per minute
```

Always benchmark the actual services: heavy compilers, package downloads and
large Docker contexts can be much slower than the reference service.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Domain works incorrectly, IP works | forwarded scheme not trusted | configure proxy headers and trusted host |
| CSP blocks `http://.../admin/gitea-credentials` | SelfAD generated HTTP URL behind HTTPS | fix `X-Forwarded-Proto` trust |
| Gitea links show localhost | public Gitea URL not configured | set Gitea public/domain env and recreate |
| Direct IP:8000/8929 still responds | origins bound to all interfaces | bind both HTTP ports to `127.0.0.1` |
| SSH says unauthorized | wrong repo or service not issued | verify assignment/collaborator and clone participant repos |
| HTTPS Git clone fails | HTTP Git intentionally disabled | use SSH port 2224 |
| Exploit always says failed | inspect last output/traceback line | fix imports, target URL or exit code |
| Defense failed X/Y | jury exploit still recovers flags | patch vulnerability without breaking checker |
| Event stuck PROCESSING | interrupted runner command/old version | update; restart recovers interrupted events |
| Queue grows | runner CPU/build throughput exhausted | keep safe concurrency, add runner CPU |
| Caddy certificate failure | DNS/80/443 not reachable | fix DNS/firewall, inspect Caddy logs |
| Disk fills | images/build cache/volumes | inspect `docker system df`, prune without volumes |
| `SELFAD_TRUSTED_PROXY_HOSTS` error | proxy trust enabled without hosts in custom topology | set the actual proxy peer address |

## Known Limitations

- The event queue lives in the SelfAD database; there is no Redis/Celery or
  distributed dispatcher.
- A database-backed/file-backed ownership lock intentionally permits only one
  control-plane process per installation.
- One SelfAD process configures one Docker endpoint; runner pooling is not
  built in.
- Runner cleanup is scoped by a persistent installation label, but sharing one
  runner still shares CPU, disk and build cache and is not recommended.
- SQLite is for bundled/small events; PostgreSQL is the production scale path.
- Build-time networking is runner-host policy and differs from internal
  runtime networking.
- `runner_mode=external` does not prove host separation.
- Capacity varies heavily by service Dockerfile and dependencies.

## Project Status

- MIT licensed;
- CI runs the test suite on Python 3.11 and 3.14;
- CI also checks Ruff, mypy, dependency advisories, PostgreSQL migrations,
  Compose rendering and a complete Gitea/runner stack startup;
- security reports are accepted privately via [SECURITY.md](SECURITY.md);
- production and runner-host guides are available under [`docs/`](docs/).

Contributions should preserve the security boundaries, keep deployment
defaults conservative and add tests for behavioral or infrastructure changes.

---

<div align="center">

**Build services. Break them. Patch them. Measure everything.**

</div>
