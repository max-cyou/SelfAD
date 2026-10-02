# Dedicated runner VM

Use one fresh VM or microVM solely as the Docker runner for a tournament. It
will build and run participant-controlled code; do not place Gitea, SelfAD
data, SSH credentials or unrelated services on it.

## 1. Create mutual TLS credentials

On an offline admin machine or the control-plane host, create a fresh bundle:

```bash
./scripts/event/runner-init-tls.sh /srv/selfad/runner-1-tls runner-1.internal selfad-control
```

The command creates these separate directories:

- `runner`: server certificate/key for the runner VM;
- `control-plane`: the only three files mounted into SelfAD;
- `ca-key.pem`: CA private key. Store it offline and do not leave it on either
  running host after distribution.

The runner DNS name or IP passed to the command must exactly match
`SELFAD_RUNNER_DOCKER_HOST`; it is placed in the server certificate SAN.

## 2. Configure Docker on the VM

Install Docker from the VM distribution, copy `runner` to
`/etc/docker/selfad-tls`, owned by root and mode `0700`. Create a systemd
override for the Docker service. The exact Docker binary path may differ by
distribution; verify it with `systemctl cat docker` first.

```ini
# /etc/systemd/system/docker.service.d/selfad-tls.conf
[Service]
ExecStart=
ExecStart=/usr/bin/dockerd -H fd:// -H tcp://0.0.0.0:2376 --tlsverify --tlscacert=/etc/docker/selfad-tls/ca.pem --tlscert=/etc/docker/selfad-tls/cert.pem --tlskey=/etc/docker/selfad-tls/key.pem
```

Then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart docker
```

From the control plane, verify the client bundle before starting SelfAD:

```bash
sudo env \
  DOCKER_HOST=tcp://runner-1.internal:2376 \
  DOCKER_TLS_VERIFY=1 \
  DOCKER_CERT_PATH=/srv/selfad/runner-1-tls/control-plane \
  docker version
```

Never expose port `2376` without TLS. Firewall it so only the control-plane IP
can reach it. Do not route it through a public reverse proxy.

For a fresh Debian/Ubuntu runner, the repository includes the equivalent
guarded installer. It refuses a host with existing Docker containers, enables
Docker user namespaces, disables default bridge inter-container communication,
and only proceeds if it can add a UFW allow rule or you explicitly confirm an
equivalent cloud/nftables firewall rule.

```bash
SELFAD_RUNNER_FIREWALL_CONFIRMED=true \
  sudo ./scripts/event/runner-install.sh /path/to/runner 10.0.0.10
```

Use `SELFAD_RUNNER_FIREWALL_CONFIRMED=true` only after configuring an external
firewall to allow only the control plane (for example `10.0.0.10`) to reach
runner TCP port `2376`, and deny every other source.

## 3. Connect the control plane

Copy the `control-plane` directory (not `ca-key.pem`) to the public control
plane as `/opt/selfad/runner-tls`. The SelfAD process uses the fixed container
UID `10001`, so make the bundle readable only by that UID:

```bash
sudo chown -R 10001:10001 /opt/selfad/runner-tls
sudo chmod 0700 /opt/selfad/runner-tls
sudo chmod 0600 /opt/selfad/runner-tls/key.pem
sudo chmod 0644 /opt/selfad/runner-tls/ca.pem /opt/selfad/runner-tls/cert.pem
```

In `/opt/selfad/.env`, set:

```dotenv
SELFAD_RUNNER_DOCKER_HOST=tcp://runner-1.internal:2376
SELFAD_RUNNER_TLS_VERIFY=true
SELFAD_RUNNER_TLS_DIR=/opt/selfad/runner-tls
```

Then rebuild/restart only SelfAD and check readiness:

```bash
cd /opt/selfad
docker compose -f docker-compose.production.yml up -d --build selfad
curl --fail https://ctf.example/ready
```

The response must contain `"runner":true` and `"runner_mode":"external"`.

## 4. Network and lifecycle

Allow runner-host egress only to approved image/package mirrors for image
builds. SelfAD creates each submitted job on an internal Docker network, so
running service, jury and exploit containers do not get internet access.

Pre-pull `python:3.13-alpine` and all approved service base images before the
event. Rebuild the VM after the event, and keep a second clean VM image ready
for replacement during the event.
