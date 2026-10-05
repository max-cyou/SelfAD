# Changelog

All notable changes to SelfAD are documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-05

Initial public release.

### Added

- FastAPI organiser panel with first-run setup, participant registration,
  invite codes, scheduling and a live scoreboard.
- Private Gitea repositories for service sources, attacks and defenses.
- Draft, ready-to-issue and active service lifecycle with access granted only
  on activation.
- Isolated Docker-based checker pipeline with signed webhooks, resource limits,
  immutable Dockerfiles and automatic cleanup.
- Coverage and per-flag attack/defense scoring, configurable attempt penalties
  and deterministic scoreboard ordering.
- SQLite support for local deployments and PostgreSQL with Alembic migrations
  for production deployments.
- Local, HTTPS and separate-runner Compose deployments.
- Public-event preflight, backup, restore, reset, diagnostics and smoke-test
  tooling.
- Production hardening guidance and a dedicated runner VM installation path.

### Security

- CSRF protection, signed sessions, security headers and rate limiting.
- HMAC-authenticated Gitea webhooks with delivery deduplication.
- Fail-closed public-event configuration validation.
- Mutually authenticated TLS support for a dedicated remote Docker runner.

[0.1.0]: https://github.com/max-cyou/SelfAD/releases/tag/v0.1.0
