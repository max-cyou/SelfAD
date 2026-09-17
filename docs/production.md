# Production deployment

The single-container setup is for local development and small trusted tests only.
It starts an internal Docker daemon and therefore requires `--privileged`.
Do not expose that mode to untrusted participants.

## Required topology

Run the control plane and the runner on separate machines or VMs:

- **Control plane:** SelfAD, Gitea, a PostgreSQL database for SelfAD and the
  persistent `selfad-data` volume. It is not privileged and has no local
  Docker socket.
- **Runner VM:** a disposable, dedicated host reachable only from the control
  plane over mutually authenticated TLS. It must not contain tournament data,
  Gitea credentials, SSH keys or other workloads.

The runner host is a blast boundary. A malicious Dockerfile can at most
compromise that runner, which must be rebuilt or replaced after an event.
For public internet events, use a VM or microVM per runner job (for example
Firecracker, Kata Containers or a cloud VM pool) rather than treating Docker
containers as a complete security boundary.

SelfAD labels every temporary runner container, network and image. On a
control-plane restart it removes only resources carrying that label and returns
interrupted checks to the queue. Do not use the same runner for unrelated
workloads.

## Control-plane start

For the supported production profile, copy `.env.production.example` to `.env`,
replace every secret and public URL, then run:

```bash
docker compose -f docker-compose.production.yml up -d --build
```

This creates a dedicated PostgreSQL volume and keeps it off the public network.
The control plane is deliberately **not** privileged and does not mount a Docker
socket. It requires a separate runner endpoint and its client TLS directory.
Put the three runner client files in the directory named by
`SELFAD_RUNNER_TLS_DIR`.

The compose profile includes Caddy for TLS on ports 80/443. SelfAD and Gitea
HTTP ports are intentionally not published directly; Caddy is the sole trusted
reverse proxy on a dedicated internal network. Point both public DNS records at
this host before starting it, and allow inbound 80/443 plus the configured
Gitea SSH port in the host firewall.

Set `SELFAD_GITEA_PUBLIC_URL`, `SELFAD_GITEA_DOMAIN`,
`SELFAD_GITEA_SSH_DOMAIN` and `SELFAD_GITEA_SSH_PUBLIC_PORT` to the values
participants actually use. They control clone URLs displayed by Gitea; leaving
the image defaults would publish `localhost` links.

The runner TLS directory must contain `ca.pem`, `cert.pem` and `key.pem`.
Do not publish Docker TCP without TLS, and firewall it so only the SelfAD
control-plane address can reach it.

`GET /ready` must return HTTP 200 before opening registration. It verifies the
SelfAD database, Gitea API token and configured runner.

Set a long random `SELFAD_METRICS_TOKEN` and scrape `GET /metrics` with
`Authorization: Bearer <token>`. The endpoint exposes queue state, issued
services and whether the current process uses an internal or external runner.

SelfAD also applies per-process limits of 12 sign-in attempts and 6
registrations per IP per minute by default. Tune them with
`SELFAD_LOGIN_RATE_LIMIT` and `SELFAD_REGISTRATION_RATE_LIMIT`; keep a stricter
network-level rate limit in the reverse proxy because an in-process limit is
not shared between replicas.

`SELFAD_WORKER_CONCURRENCY` defaults to `1`, which is appropriate for a local
machine and one runner. Increase it only after measuring the capacity of the
single configured runner endpoint. A pool of runner VMs requires a dispatcher
or multiple isolated control-plane instances; SelfAD does not silently spread
one process across arbitrary Docker daemons. Set matching CPU/RAM quotas at the
runner level rather than overcommitting the control plane.

SelfAD uses PostgreSQL when `SELFAD_DATABASE_URL` is supplied. Create a
dedicated `selfad` database and account; do not share it with Gitea, because
both applications own tables such as `users` and `services`.

## Runner host

Use the [dedicated runner VM guide](runner-host.md) to create its mutual TLS
credentials and Docker listener.

Use a fresh VM dedicated to one tournament. Enable Docker's TLS listener only
on a private interface. The runner needs outbound access only to the image and
package mirrors you explicitly allow. Participant jobs are placed on Docker
networks with no external route, run with no Linux capabilities, read-only
root filesystems, PID/CPU/RAM limits and no-new-privileges.

Build-time network access is still a runner-host concern. Prefer prebuilt base
images and an internal package mirror; never give the runner access to control
plane volumes or host Docker sockets.

## Before every event

1. Stop the control plane, then create and test a backup of `selfad-data`:

   ```bash
   ./scripts/backup-volume.sh selfad-data /srv/backups/selfad
   ```

   Keep the archive off the control-plane host as well. Restore only onto a
   fresh volume after testing the archive in an isolated environment:

   ```bash
   ./scripts/restore-volume.sh /srv/backups/selfad/selfad-data-YYYYMMDDTHHMMSSZ.tar.gz selfad-data-restore-test
   ```

   The restore script refuses to overwrite an existing volume. Start a
   disposable SelfAD instance using `selfad-data-restore-test` before relying
   on the backup for an event.
2. Verify `https://ctf.example/ready` is `200` and reports `runner_mode` as
   `external`, preferably with the repeatable preflight check:

   ```bash
   SELFAD_METRICS_TOKEN='your-metrics-token' \
     ./scripts/event-preflight.sh https://ctf.example
   ```
3. Push and check a known vulnerable service, an exploit and a one-line fix
   through the real participant path.
4. Rebuild the runner VM from a known image and pre-pull required base images.
5. Keep a second clean runner VM ready for replacement.

## After the event

Archive logs and the `selfad-data` volume, then destroy or rebuild every runner
VM. Treat a runner as compromised after it has built or executed participant
content.
