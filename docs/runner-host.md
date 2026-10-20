# Dedicated runner VM

Use a fresh, disposable VM solely for participant-controlled builds and code.
Never put SelfAD, Gitea, their database, SSH keys or unrelated services on it.
Docker containers share the runner kernel: these checks cannot prove the
absence of Docker, BuildKit or kernel container-escape vulnerabilities.

## Provisioning

Follow the complete two-machine deployment instructions in the [README](../README.md).
Requirements: Docker Engine 28+, systemd, cgroup v2, user namespace support,
seccomp, Python 3, iptables and ip6tables. Use a separately mounted filesystem
at `/var/lib/docker` (or an explicit `SELFAD_RUNNER_DATA_ROOT=/srv/...`). It
must not be a bind mount of the OS filesystem. Size it for approved images and
the event: filling Docker storage must not fill the OS disk. Provision disks
through your provider; the installer never formats a disk.

Generate credentials on the control plane:

```bash
./scripts/event/runner-init-tls.sh /srv/selfad/runner-1-tls runner-1.internal selfad-control
```

Copy only the `runner` directory to the runner VM. Keep `control-plane` on the
control plane; back up the CA private key offline and remove it from live hosts.
The runner address must match the certificate SAN. Keep both installer scripts
together and run on the fresh runner:

```bash
sudo ./scripts/event/runner-install.sh /path/to/runner 10.0.0.10
```

The installer refuses existing containers and configures mTLS, user namespace
remapping, private cgroup namespaces, `icc=false` and bounded local logs. It
disables the containerd image store so image data remains under the separate
Docker data root (and is compatible with user namespace remapping). Its
`runner-firewall.sh` systemd service prevents job bridges (`sad-*`) reaching
host-input services, denies plaintext Docker API, and limits TLS port 2376 to
the control-plane IPv4. IPv6 API access is denied. It never flushes firewall
chains or changes SSH rules. With a cloud firewall instead of active UFW,
restrict 2376 to the control plane first, then run with
`sudo SELFAD_RUNNER_FIREWALL_CONFIRMED=true ...`. Never proxy the Docker API
through Cloudflare or another public reverse proxy.

The client key grants powerful control of the dedicated runner. Mount only
the client bundle into SelfAD, readable by UID 10001, directory mode 0700,
key mode 0600. Never mount it or a Docker socket into task containers.

## Initialize and verify

Control-plane `.env`:

```dotenv
SELFAD_PUBLIC_EVENT_MODE=true
SELFAD_RUNNER_DOCKER_HOST=tcp://runner-1.internal:2376
SELFAD_RUNNER_TLS_DIR=/srv/selfad/runner-1-tls/control-plane
SELFAD_RUNNER_ISOLATION=dedicated-host
SELFAD_RUNNER_BUILDER=selfad-secure
SELFAD_JURY_IMAGE=python:3.13-alpine
```

From the repository on the control plane:

```bash
docker compose -f docker-compose.production.yml up -d --build selfad
docker compose -f docker-compose.production.yml exec --user 10001 selfad \
  /opt/selfad/venv/bin/python -m selfad.runtime.build --initialize
docker compose -f docker-compose.production.yml exec --user 10001 selfad \
  sh /app/scripts/event/runner-isolation-check.sh --control-host 10.0.0.10
curl --fail https://ctf.example/ready
```

Replace the example control-plane IP and domain. Readiness must report
`"runner":true` and `"runner_mode":"external"`. The isolation check must exit
zero and print its final PASS. It checks valid mTLS access, rejection without
a client certificate, denied plaintext API, positive same-job connectivity,
cross-job/host/control-plane/API/metadata/internet/DNS isolation, actual cgroup
limits, denied mount operations, read-only rootfs, missing credentials/socket,
and bounded OOM/PID enforcement. A real build explicitly requests
`RUN --network=default` to check Dockerfile overrides. Run before the event:
memory/PID exhaustion probes are intentional, not suitable during a round.

The application bundles Buildx 0.38.0. Initialization creates BuildKit
`moby/buildkit:v0.33.1`, container `buildx_buildkit_selfad-secure0`, context
`selfad-secure-runner`, and volume `buildx_buildkit_selfad-secure0_state`.
The context copies mTLS credentials into the private control-plane work
directory: treat it as sensitive. Existing unsafe configurations are refused,
not silently replaced. For key rotation or incompatible configuration, stop
submissions and provision a newly named builder/context after reviewing old
resources. Never broadly prune Docker resources during an event.

After restarting the builder/runner, rerun `--initialize` and the live check:
the in-namespace firewall needs restoration and tmpfs cache is ephemeral.
Readiness/builds fail closed while the firewall is absent. Keep Docker, kernel
and pinned BuildKit/Buildx versions security-patched; prefer approved image
digests over mutable tags.

## Boundaries and compatibility

| Component | Boundary |
| --- | --- |
| BuildKit and child build steps | 4 GiB builder RAM, no swap, 2 CPUs, 512 PIDs; private cgroup namespace; per-step CPU/RAM/file limits |
| Build state/cache | 4 GiB tmpfs volume; no host bind mounts or task-accessible client credentials |
| Build networking | Default `none`; IPv4/IPv6 firewall inside builder blocks forwarding and new non-loopback input; insecure entitlements disabled |
| Image export | 512 MiB client archive cap, 1 GiB logical expanded files, 100,000 layer entries; validation before Docker load |
| Runner images | Conservative 16 GiB sum of logical image sizes before builds; operator cleanup when exhausted |
| Task runtime | Forced UID/GID 10001, no capabilities, no-new-privileges, seccomp, read-only rootfs, bounded tmpfs/CPU/RAM/swap/PIDs/open files; no core dumps |
| Runtime networking | Separate internal IPv4-only job networks, isolated gateway mode, host-input firewall |
| Logs | Local driver, 5 MiB × 2 files per container |

The image-size budget is not a physical filesystem quota: shared layers,
metadata and approved images still consume space. The separate Docker
filesystem is the final host storage boundary. Keep enough control-plane
working space for bounded exports and existing repository data. Recalculate
total memory before raising worker concurrency.

Task images must work as UID 10001, bind unprivileged ports and write only to
configured tmpfs locations. Images declaring `VOLUME` are rejected to avoid
implicit writable volumes. Test each service's full jury cycle before opening
registration.

Public builds cannot run online apt/pip/npm installs. Prebuild trusted base
images containing compilers/dependencies or vendor dependencies. Bake approved
exploit/jury Python packages into `SELFAD_JURY_IMAGE` and leave fixed/participant
attack requirements empty. Publish trusted base images to an approved registry:
BuildKit does not automatically see Docker Engine's locally loaded images.
Registry/image resolution still uses the trusted daemon's egress; restrict it
to approved registries with infrastructure firewall/proxy policy. Blocking
build-step networking does not make the entire daemon air-gapped.

BuildKit's daemon container is privileged by the Docker-container driver;
participant build steps are not. A dedicated disposable VM remains required
even after all probes pass. Private development without public mode or
`SELFAD_RUNNER_BUILDER` retains the legacy unrestricted Docker build path and
is **not safe for untrusted public submissions**. Rebuild the runner VM after
the event and keep a clean replacement ready.
