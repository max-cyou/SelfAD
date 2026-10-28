# Changelog

All notable changes to SelfAD are documented in this file.

The project follows [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-20

### Added

- Browser-based CodeMirror editor for attack and defense repositories, with
  syntax highlighting, unsaved-change indicators, `Ctrl+S`, Markdown previews
  and **Save all & Submit** to commit, push and queue a check.
- Editor sidebar for adding and removing Git SSH keys and copying clone commands.
- Participant submission history tied to commit SHAs, with results, scores and
  penalties. Administrators can filter all participants' submissions and inspect
  recorded diagnostics.
- Administrator setting for public Gitea web access, disabled by default.
- Single-VPS runner profile for trusted deployments and a documented production
  deployment example based on J2W Autumn CTF.
- Live runner checks for mTLS authentication, build and runtime network
  isolation, and actual resource-limit enforcement.

### Changed

- Reorganized the admin panel into collapsible settings sections and a separate
  participant-management view.
- Removed the SSH-key field from registration; keys can be managed in the editor.
- Split Gitea integration, Docker runtime, workers and admin templates into
  smaller modules while retaining their public interfaces.
- Snapshot canonical repository refs and scoring rules when accepting a
  submission. Accepted checks can finish after the contest deadline.
- Lock numeric scoring rules once the contest starts, a submission is admitted
  or scoring history exists.

### Fixed

- Push-processing races: checks evaluate the submitted commit rather than a
  moving branch HEAD, and stale workers cannot overwrite newer results.
- Duplicate scoring from repeated webhook deliveries or interrupted workers.
- Interrupted checks now recover through leased worker ownership and bounded
  infrastructure retries without recording scoring attempts or penalties.
- Editor save-shortcut handling and links from service cards to the editor.
- Docker build-context exclusions for required event scripts and missing
  PostgreSQL CI test dependencies.

### Security

- Added a verified, resource-limited BuildKit builder for public events, with
  bounded RAM, CPU, PIDs, tmpfs build state and image exports.
- Strengthened build and runtime network isolation, including protection against
  Dockerfile network overrides and job access to runner-host services.
- Added runner firewall provisioning, bounded container logs and image-storage
  budget checks.
- Force service containers to run as UID/GID `10001:10001` and reject image
  `VOLUME` declarations that would create implicit writable volumes.
- Public-event readiness and builds fail closed when required builder
  protections are missing.

### Upgrade notes

- Back up the database and persistent data before upgrading. This release adds
  Alembic migrations for Gitea access settings, worker recovery and submission
  admission.
- Public events require Docker Engine 28+, cgroup v2 and a dedicated disposable
  runner VM configured according to the [runner guide](docs/runner-host.md).
  Initialize the secure builder and run the live isolation checks before
  accepting submissions.
- Public builds no longer support online package installation. Preinstall
  dependencies in trusted base images or vendor them; bake approved Python
  packages into `SELFAD_JURY_IMAGE` and leave attack requirements empty.
- Task images must support the unprivileged runtime user, unprivileged ports
  and bounded tmpfs write locations, without `VOLUME` declarations.
- Container isolation checks do not guarantee protection against Docker,
  BuildKit or kernel vulnerabilities. Single-host deployments remain intended
  for development and trusted private events, not public untrusted workloads.

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

[1.0.0]: https://github.com/max-cyou/SelfAD/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/max-cyou/SelfAD/releases/tag/v0.1.0
