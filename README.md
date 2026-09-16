# SelfAD

Personal Attack–Defense platform.

## Local development

This starts a privileged internal Docker runner. It is for a local machine or a
small trusted test only, never for a public CTF.

```bash
docker build -t selfad .
docker run --rm --privileged --cgroupns=host \
  -e SELFAD_ENABLE_INTERNAL_RUNNER=true \
  -v selfad-data:/data \
  -p 8000:8000 -p 8929:8929 -p 2224:22 \
  selfad
```

The `selfad-data` volume keeps the instance database between container runs.

For a real event, use an unprivileged control plane and an isolated external
runner. See [production deployment](docs/production.md).
