#!/usr/bin/env python3
"""Exercise the real Gitea-to-runner path with disposable repositories.

Run inside the SelfAD control-plane container, where its private Gitea token
and runner mTLS configuration are available. It creates only uniquely named
private repositories and removes them in ``finally``.
"""

from __future__ import annotations

import secrets
import sys

from selfad.gitea import create_repository, create_repository_file, delete_repository
from selfad.runner import run_service_runtime_check
from selfad.service_contract import validate_service_contract
from selfad.settings import get_gitea_settings


BRANCH = "main"
FLAG = "ABCDEFGHIJKLMNOPQRSTUVWX12345678"
SERVICE_DOCKERFILE = b"""FROM python:3.13-alpine
WORKDIR /app
COPY app.py .
EXPOSE 8080
CMD [\"python\", \"app.py\"]
"""
SERVICE_CONFIG = b"""version: 1
service:
  port: 8080
  healthcheck: /health
"""
VULNERABLE_APP = b'''import json
from http.server import BaseHTTPRequestHandler, HTTPServer

flags = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        elif self.path == "/vulnerable":
            self.send_response(200); self.end_headers(); self.wfile.write("\\n".join(flags).encode())
        elif self.path == "/api/items":
            self.send_response(200); self.end_headers(); self.wfile.write(b"[]")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/flags" and isinstance(body.get("flag"), str):
            flags.append(body["flag"])
            self.send_response(201); self.end_headers()
        elif self.path == "/api/items":
            self.send_response(201); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
'''
PATCHED_APP = VULNERABLE_APP.replace(
    b'''elif self.path == "/vulnerable":
            self.send_response(200); self.end_headers(); self.wfile.write("\\n".join(flags).encode())''',
    b'''elif self.path == "/vulnerable":
            self.send_response(403); self.end_headers()''',
)
INJECT = f'''import json
import urllib.request

flag = "{FLAG}"
request = urllib.request.Request(
    "http://target:8080/api/flags",
    data=json.dumps({{"flag": flag}}).encode(),
    headers={{"Content-Type": "application/json"}},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    assert response.status == 201
print(flag)
'''.encode()
EXPLOIT = b'''import urllib.error
import urllib.request

try:
    with urllib.request.urlopen("http://target:8080/vulnerable", timeout=5) as response:
        print(response.read().decode())
except urllib.error.HTTPError as error:
    if error.code != 403:
        raise
'''
CHECKER = b'''import json
import urllib.request

request = urllib.request.Request(
    "http://target:8080/api/items",
    data=json.dumps({"value": "smoke"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=5) as response:
    assert response.status == 201
'''
ATTACK_DOCKERFILE = b'FROM python:3.13-alpine\nWORKDIR /workspace\nCMD ["python", "exploit.py"]\n'


def seed(settings, name: str, files: dict[str, bytes]) -> str:
    repository = create_repository(
        settings,
        path=name,
        description="SelfAD disposable runner smoke test",
        default_branch=BRANCH,
    )
    for path, content in files.items():
        create_repository_file(
            settings,
            repository.path,
            path,
            content=content,
            branch=BRANCH,
            message="Seed runner smoke test",
        )
    return repository.path


def assert_result(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    settings = get_gitea_settings()
    suffix = secrets.token_hex(5)
    repositories: list[str] = []
    try:
        service = seed(
            settings,
            f"selfad-smoke-{suffix}-service",
            {
                "Dockerfile": SERVICE_DOCKERFILE,
                "selfad.yml": SERVICE_CONFIG,
                "app.py": VULNERABLE_APP,
            },
        )
        repositories.append(service)
        jury = seed(
            settings,
            f"selfad-smoke-{suffix}-jury",
            {"inject.py": INJECT, "exploit.py": EXPLOIT, "checker.py": CHECKER},
        )
        repositories.append(jury)
        attack = seed(
            settings,
            f"selfad-smoke-{suffix}-attack",
            {"Dockerfile": ATTACK_DOCKERFILE, "exploit.py": EXPLOIT},
        )
        repositories.append(attack)
        defense = seed(
            settings,
            f"selfad-smoke-{suffix}-defense",
            {
                "Dockerfile": SERVICE_DOCKERFILE,
                "selfad.yml": SERVICE_CONFIG,
                "app.py": PATCHED_APP,
            },
        )
        repositories.append(defense)

        contract = validate_service_contract(
            settings,
            repository_path=service,
            jury_repository_path=jury,
            default_branch=BRANCH,
        )
        assert_result(contract.valid, f"contract rejected: {contract.message}")

        canonical = run_service_runtime_check(
            settings,
            repository_path=service,
            jury_repository_path=jury,
            contract=contract,
            cache_service_image=True,
        )
        assert_result(
            canonical.passed and canonical.matched_flags == 1,
            f"canonical check failed: {canonical.message}\n{canonical.log}",
        )

        from selfad.gitea import get_branch_commit

        attack_commit = get_branch_commit(settings, attack, branch=BRANCH)
        attack_result = run_service_runtime_check(
            settings,
            repository_path=service,
            jury_repository_path=jury,
            contract=contract,
            exploit_repository_path=attack,
            exploit_commit=attack_commit,
            cache_service_image=True,
        )
        assert_result(
            attack_result.passed and attack_result.matched_flags == 1,
            f"attack check failed: {attack_result.message}\n{attack_result.log}",
        )

        defense_contract = validate_service_contract(
            settings,
            repository_path=defense,
            jury_repository_path=jury,
            default_branch=BRANCH,
        )
        assert_result(defense_contract.valid, f"defense contract rejected: {defense_contract.message}")
        defense_result = run_service_runtime_check(
            settings,
            repository_path=defense,
            jury_repository_path=jury,
            contract=defense_contract,
        )
        assert_result(
            defense_result.completed
            and defense_result.functionality_passed
            and not defense_result.passed
            and defense_result.injected_flags == 1,
            f"defense check failed: {defense_result.message}\n{defense_result.log}",
        )
        print("runner smoke passed: canonical attack and patched defense")
        return 0
    finally:
        for repository_path in reversed(repositories):
            try:
                delete_repository(settings, repository_path)
            except Exception as error:  # Cleanup must not hide the primary result.
                print(f"cleanup warning for {repository_path}: {error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
