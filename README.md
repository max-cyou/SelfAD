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

The bundled runner is suitable for local development and test events. It runs
untrusted build and exploit code in Docker-in-Docker, so a public CTF should
use the separate-runner production topology below.

For a real event, use an unprivileged control plane and an isolated external
runner. See [production deployment](docs/production.md).
