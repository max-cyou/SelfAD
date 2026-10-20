# Module map

SelfAD runs one control-plane process with several repository worker threads.
The queue and scores live in the database; Gitea stores the source repositories
and Docker executes checks.

## Gitea integration

`selfad/gitea.py` preserves the existing import API. Implementations live in
`selfad/gitea_api/`:

| Module | Responsibility |
| --- | --- |
| `types.py` | Repository/file/account records and error types |
| `client.py` | Authenticated JSON requests and repository response parsing |
| `repositories.py` | Repository lifecycle, branch commits, collaborators and webhooks |
| `files.py` | File reads, trees, atomic changes and archive downloads |
| `users.py` | Accounts, authentication and SSH keys |
| `provisioning.py` | Create and initialize the organizer's service and jury repositories |

These modules import each other directly. They do not import the public facade,
which avoids circular imports.

## Docker runtime

`selfad/runner.py` preserves the runtime API. Implementations live in
`selfad/runtime/`:

| Module | Responsibility |
| --- | --- |
| `types.py` | Result records, errors, limits and the shared cache build lock |
| `process.py` | Bounded subprocess execution, timeouts and termination |
| `docker.py` | Docker CLI environment, labels, availability and cleanup |
| `archive.py` | Safe repository archive extraction |
| `requirements.py` | Dependency validation and Python runtime image preparation |
| `cache.py` | Canonical service image names and pruning |
| `sandbox.py` | Restricted script execution and service health probes |
| `output.py` | Flag parsing, stdout noise and log formatting |
| `check.py` | Build, start, check functionality, inject flags and execute exploits |

Runtime isolation and resource limits belong in `sandbox.py` and `check.py`.
The archive and process limits are enforced by their respective modules.

## Repository workers

`selfad/worker.py` owns the worker loop and canonical service validation/runtime
checks. Supporting modules live in `selfad/workers/`:

| Module | Responsibility |
| --- | --- |
| `types.py` | Immutable batch identifiers, generations and commit SHAs |
| `queue.py` | Claim/recover events, detect superseded batches and finish events |
| `recovery.py` | Heartbeat leases, stale-worker fencing and bounded delayed retries |
| `participant.py` | Attack/defense checks and submission score persistence |

The worker calls the Gitea and runtime APIs. `selfad/scoring.py` remains
responsible for score formulas and attempt history; it does not execute code.
The module extraction does not change database schema, queue policy or scoring.

Worker recovery migration `0004` adds `heartbeat_at`, `retry_after` and
`scored_at` to repository events. All result transactions first conditionally
update processing events by ownership token and unexpired lease. Participant
scoring additionally sets `scored_at` in the same transaction as score/attempt
updates and event completion. This marker survives audit-tail pruning. Runtime
transport failures are typed separately from contestant failures; an independent
daemon probe prevents contestant output from spoofing a free retry.

## Administration templates

`selfad/routes/submissions.py` serves the shared `/submissions` page. Administrators
see all participants; participant queries are scoped by the authenticated user's
ID and omit diagnostic messages at the SQL level. Service dropdowns and record
counts use the same scope, and participant filters cannot override it.
`selfad/submissions.py` reads retained evaluated attempts and in-flight pushes
with SQL filtering and pagination. The page does not rerun checks or change
scoring. Attempt diagnostics are existing recorded messages, not full runtime
logs; exact durations are unavailable without an event/attempt linkage.

`selfad/templates/admin.html` is the page shell. Section templates and their
form fragments live under `selfad/templates/admin/`:

- `general/` contains registration, contest state, scoring, penalties, attack
  dependencies and Git access. Optional settings use native `details` blocks;
  collapsed fields remain enabled and are submitted by the shared form.
- `services/` separates the repository list from the create/edit form.
- `users/` contains the account editor and Gitea administrator credentials.
- `appearance/` separates identity, palette and home-page templates.
- `sidebar.html` owns section navigation.

Server validation opens the affected settings modules. `static/admin.js`
updates summaries and opens parent blocks for browser validation errors.

## Verification

Run from the repository root after installing `requirements-dev.txt`:

```bash
python -m unittest discover -s tests -v
ruff check selfad tests migrations main.py
mypy
```

When mocking a dependency, patch the module that uses it. For example, file API
tests patch `selfad.gitea_api.files._request`, sandbox tests patch
`selfad.runtime.sandbox._docker`, and queue tests patch
`selfad.workers.queue.SessionLocal`. Patching a facade export does not replace
the dependency inside its implementation module.
