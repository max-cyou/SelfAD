# SelfAD

Personal Attack–Defense platform.

## Quick start

Requires Docker Engine with Docker Compose v2. The installer starts SelfAD,
local Gitea and an isolated Docker runner, then waits until all three are ready.

```bash
git clone https://github.com/max-cyou/SelfAD.git
cd SelfAD
./scripts/install.sh
```

Open `http://localhost:8000` and complete the setup form. The organiser's
username and password work in both SelfAD and Gitea. Data remains in Docker
volumes after restarts.

To let devices on the LAN clone repositories with the correct address, install
with the host's LAN address:

```bash
./scripts/install.sh --host 192.168.1.154
```

Useful commands:

```bash
./scripts/doctor.sh                  # check panel, Gitea and runner
./scripts/update.sh                  # rebuild after a git pull
./scripts/backup.sh /path/to/backups # archive SelfAD data
docker compose down                  # stop without deleting data
```

## Domain and HTTPS

The panel speaks plain HTTP on the published port. To serve it on a domain,
keep your own nginx or Caddy on the Docker host terminating TLS for the domain
and forwarding to the panel, then tell SelfAD to trust that proxy so it renders
`https://` links. In `.env`:

```dotenv
SELFAD_TRUST_PROXY_HEADERS=true
# Address of the proxy as the container sees it; for a proxy on the Docker
# host this is the compose network gateway: docker network inspect selfad_default
SELFAD_TRUSTED_PROXY_HOSTS=192.168.0.1
SELFAD_GITEA_PUBLIC_URL=https://git.ctf.example
SELFAD_GITEA_DOMAIN=git.ctf.example
SELFAD_GITEA_SSH_DOMAIN=ctf.example
SELFAD_HTTP_BIND=127.0.0.1
SELFAD_GITEA_HTTP_BIND=127.0.0.1
```

The proxy must pass `X-Forwarded-Proto` (a standard `proxy_set_header` setup
does). Restart with `docker compose up -d`. The loopback binds keep outsiders
from bypassing the proxy; leave them out while you still need direct
`http://IP:8000` access.

The bundled runner is suitable for local development and test events. It runs
untrusted build and exploit code in Docker-in-Docker, so a public CTF should
use the separate-runner production topology below.

For a real event, use an unprivileged control plane and an isolated external
runner. See [production deployment](docs/production.md).
