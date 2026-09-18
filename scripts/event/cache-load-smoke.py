#!/usr/bin/env python3
"""Benchmark concurrent attacks reusing one cached canonical service image."""

from __future__ import annotations

import argparse
import concurrent.futures
import secrets
import time

from runner_smoke import (
    ATTACK_DOCKERFILE,
    BRANCH,
    CHECKER,
    EXPLOIT,
    INJECT,
    SERVICE_CONFIG,
    SERVICE_DOCKERFILE,
    VULNERABLE_APP,
    seed,
)
from selfad.gitea import delete_repository, get_branch_commit
from selfad.runner import run_service_runtime_check
from selfad.service_contract import validate_service_contract
from selfad.settings import get_gitea_settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--checks", type=int, default=8)
    parser.add_argument("--no-service-cache", action="store_true")
    args = parser.parse_args()
    settings = get_gitea_settings()
    suffix = secrets.token_hex(5)
    repositories: list[str] = []
    try:
        source = seed(settings, f"selfad-cache-{suffix}-service", {
            "Dockerfile": SERVICE_DOCKERFILE,
            "selfad.yml": SERVICE_CONFIG,
            "app.py": VULNERABLE_APP,
        })
        jury = seed(settings, f"selfad-cache-{suffix}-jury", {
            "inject.py": INJECT, "exploit.py": EXPLOIT, "checker.py": CHECKER,
        })
        attack = seed(settings, f"selfad-cache-{suffix}-attack", {
            "Dockerfile": ATTACK_DOCKERFILE, "exploit.py": EXPLOIT,
        })
        repositories.extend((source, jury, attack))
        contract = validate_service_contract(
            settings,
            repository_path=source,
            jury_repository_path=jury,
            default_branch=BRANCH,
        )
        if not contract.valid:
            raise RuntimeError(contract.message)
        warm_started = time.monotonic()
        canonical = run_service_runtime_check(
            settings,
            repository_path=source,
            jury_repository_path=jury,
            contract=contract,
            cache_service_image=not args.no_service_cache,
        )
        warm_seconds = time.monotonic() - warm_started
        if not canonical.passed:
            raise RuntimeError(canonical.message)
        attack_commit = get_branch_commit(settings, attack, branch=BRANCH)

        def check(_: int) -> float:
            started = time.monotonic()
            result = run_service_runtime_check(
                settings,
                repository_path=source,
                jury_repository_path=jury,
                contract=contract,
                exploit_repository_path=attack,
                exploit_commit=attack_commit,
                cache_service_image=not args.no_service_cache,
            )
            if not result.passed:
                raise RuntimeError(result.message)
            return time.monotonic() - started

        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.concurrency
        ) as executor:
            durations = list(executor.map(check, range(args.checks)))
        elapsed = time.monotonic() - started
        print(
            f"canonical={warm_seconds:.2f}s checks={args.checks} "
            f"concurrency={args.concurrency} elapsed={elapsed:.2f}s "
            f"avg={sum(durations) / len(durations):.2f}s "
            f"max={max(durations):.2f}s"
        )
        return 0
    finally:
        for repository in reversed(repositories):
            try:
                delete_repository(settings, repository)
            except Exception as error:
                print(f"cleanup warning for {repository}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
