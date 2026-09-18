# Security policy

SelfAD is an attack-defense CTF platform: it deliberately builds and runs
untrusted participant code. Reports about breaking out of that isolation are
especially welcome.

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting] on this repository.
Include reproduction steps, affected components and, where relevant, the
topology you tested (bundled single-host runner vs. the production profile
with a dedicated runner VM).

Do not open public issues for suspected security problems.

[GitHub private vulnerability reporting]:
https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-reviewing-security-vulnerabilities/about-private-vulnerability-reporting

## Scope

- The SelfAD control plane (web panel, queue, Gitea integration).
- The runner-side isolation: sandbox settings, archive extraction, resource
  limits, network policies described in `selfad/runner.py`.
- The deployment configuration shipped in this repository: Dockerfile,
  Compose files, hardening scripts.

Out of scope:

- Vulnerabilities in services, exploits or checkers written by event
  participants — attacking them is the point of the game.
- Compromises that require organiser credentials or a malicious organiser.
- The bundled single-host runner being used against the documented advice
  (see `README.md` and `docs/production.md`): it is a development convenience,
  not a security boundary for public events.

## Expectations

- We aim to respond within a week and will credit reporters in release notes
  unless asked otherwise.
- There is no bug bounty; reports are handled on a best-effort basis.
- Supported version: the latest commit on `main`.

## Deployment hardening

If you run a public event, follow `docs/production.md` (control plane without
privileged access, runner VM as a disposable blast boundary) and re-check
`scripts/event/runner-isolation-check.sh` before every event.
