# J2W Autumn CTF deployment example

This is a sanitized copy of the deployment used at
`ctf.jmp2win.xyz`. It preserves the real domains, ports, worker concurrency,
single-VPS override and nginx layout. Passwords and tokens are intentionally
replaced with placeholders.

This layout runs the Docker runner inside the SelfAD container with
`privileged: true`. It is useful for a demonstration or a trusted event, but it
is **not the recommended isolation boundary for a new public CTF**. Use the
dedicated runner described in [`../../runner-host.md`](../../runner-host.md) for
untrusted participants.

## Prepare

Create both DNS records pointing to the control-plane host:

- `ctf.jmp2win.xyz`
- `git.ctf.jmp2win.xyz`

Open TCP ports `80`, `443` and `2224`, install Docker, nginx and Certbot, then
place the SelfAD repository at `/opt/selfad`.

```bash
cd /opt/selfad
cp docs/examples/j2w-autumn-ctf/.env.example .env
chmod 0600 .env
mkdir -p /srv/selfad/runner-pwf-tls
```

Replace every `replace-with-*` value. One suitable command per secret is:

```bash
openssl rand -hex 32
```

The TLS directory is retained because the base production Compose file expects
the path. The single-VPS override disables runner TLS and uses the internal
Unix socket instead.

Obtain a certificate covering both domains, then install `nginx.conf` as the
active nginx virtual host and verify it before reloading:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Start

```bash
docker compose \
  -f docker-compose.production.yml \
  -f docs/examples/j2w-autumn-ctf/docker-compose.nginx.yml \
  -f docker-compose.single-vps.yml \
  config --quiet

docker compose \
  -f docker-compose.production.yml \
  -f docs/examples/j2w-autumn-ctf/docker-compose.nginx.yml \
  -f docker-compose.single-vps.yml \
  up -d --build
```

Initialize the instance at:

```text
https://ctf.jmp2win.xyz/setup?setup_token=<SELFAD_SETUP_TOKEN>
```

The Gitea web interface is closed by default. It can be exposed from
**Admin → General → Git interface**; Git over SSH remains available at port
`2224` while the web interface is closed.

## Verify

```bash
curl --fail https://ctf.jmp2win.xyz/health
curl --fail https://ctf.jmp2win.xyz/ready
docker compose \
  -f docker-compose.production.yml \
  -f docs/examples/j2w-autumn-ctf/docker-compose.nginx.yml \
  -f docker-compose.single-vps.yml \
  ps
```

The expected Gitea HTTPS response is `403` until the administrator enables its
public interface.
