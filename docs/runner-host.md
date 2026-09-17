# Dedicated runner VM

Use one fresh VM or microVM solely as the Docker runner for a tournament. It
will build and run participant-controlled code; do not place Gitea, SelfAD
data, SSH credentials or unrelated services on it.

## 1. Create mutual TLS credentials

On an offline admin machine or the control-plane host, create a fresh bundle:

```bash
./scripts/runner-init-tls.sh /srv/selfad/runner-1-tls runner-1.internal selfad-control
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
DOCKER_HOST=tcp://runner-1.internal:2376 \
DOCKER_TLS_VERIFY=1 \
DOCKER_CERT_PATH=/srv/selfad/runner-1-tls/control-plane \
docker version
```

Never expose port `2376` without TLS. Firewall it so only the control-plane IP
can reach it. Do not route it through a public reverse proxy.

## 3. Network and lifecycle

Allow runner-host egress only to approved image/package mirrors for image
builds. SelfAD creates each submitted job on an internal Docker network, so
running service, jury and exploit containers do not get internet access.

Pre-pull `python:3.13-alpine` and all approved service base images before the
event. Rebuild the VM after the event, and keep a second clean VM image ready
for replacement during the event.
