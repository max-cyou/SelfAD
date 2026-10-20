"""Disposable bounded live checks, not a proof against kernel vulnerabilities."""

import argparse
import base64
import json
import os
import secrets
import socket
import ssl
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from selfad.runtime.build import build_image, builder_name, verify_builder
from selfad.runtime.docker import _docker, _docker_quiet, _label_arguments
from selfad.runtime.types import JURY_IMAGE, RUNNER_LABEL, RunnerError
from selfad.settings import get_runner_settings


def check(condition, message):
    if not condition:
        raise RunnerError(message)


def call(arguments, timeout=30):
    result = _docker(arguments, timeout=timeout)
    check(result.returncode == 0, f"Isolation check failed: {result.output[:1000]}")
    return result.stdout


def options(network, name, *, memory="128m", pids="64"):
    return [
        "--name",
        name,
        *_label_arguments(RUNNER_LABEL),
        "--network",
        network,
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        "0.5",
        "--pids-limit",
        pids,
        "--ulimit",
        "nofile=128:128",
        "--ulimit",
        "core=0:0",
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--ipc",
        "none",
        "--shm-size",
        "8m",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=5m",
        "--log-opt",
        "max-file=2",
    ]


def check_api_authentication():
    settings = get_runner_settings()
    endpoint = urlsplit(settings.docker_host)
    check(
        endpoint.scheme == "tcp" and settings.tls_verify and settings.cert_path,
        "Live checks require the actual remote mTLS Docker endpoint.",
    )
    call(["info", "--format", "{{.ServerVersion}}"])
    context = ssl.create_default_context(cafile=os.path.join(settings.cert_path, "ca.pem"))
    try:
        with socket.create_connection(
            (endpoint.hostname, endpoint.port or 2376), timeout=5
        ) as sock:
            with context.wrap_socket(sock, server_hostname=endpoint.hostname) as tls:
                tls.sendall(b"GET /_ping HTTP/1.0\r\nHost: docker\r\n\r\n")
                tls.recv(1024)
    except ssl.SSLCertVerificationError:
        raise RunnerError(
            "Server certificate verification failed; this is not client-auth rejection."
        )
    except ssl.SSLError as error:
        check(
            any(
                reason in str(error)
                for reason in ("CERTIFICATE_REQUIRED", "BAD_CERTIFICATE", "HANDSHAKE_FAILURE")
            ),
            f"Unclassified TLS error: {error}",
        )
    else:
        raise RunnerError("Docker API did not reject an unauthenticated TLS client.")
    try:
        with socket.create_connection((endpoint.hostname, 2375), timeout=3):
            pass
    except OSError:
        return
    raise RunnerError("Plaintext Docker port 2375 is reachable.")


RUNTIME_PROBE = """import ctypes,errno,json,os,socket,sys,resource
assert os.getuid()==10001
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
assert int(status['CapEff'].strip(),16)==0
assert status['NoNewPrivs'].strip()=='1' and status['Seccomp'].strip()=='2'
assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(128,128)
uid_map=open('/proc/self/uid_map').read().splitlines()
assert uid_map and int(uid_map[0].split()[1])!=0,'user namespace remapping missing'
if os.path.exists('/sys/fs/cgroup/cpu.max'):
 quota,period=open('/sys/fs/cgroup/cpu.max').read().split()
 assert quota!='max' and 0<int(quota)/int(period)<=0.5
 assert int(open('/sys/fs/cgroup/memory.max').read())==128*1024*1024
 assert int(open('/sys/fs/cgroup/pids.max').read())==64
else:
 roots=['/sys/fs/cgroup/cpu','/sys/fs/cgroup/cpu,cpuacct']
 root=next(path for path in roots if os.path.exists(path+'/cpu.cfs_quota_us'))
 assert 0<int(open(root+'/cpu.cfs_quota_us').read())/int(open(root+'/cpu.cfs_period_us').read())<=0.5
for path in ('/var/run/docker.sock','/run/docker.sock','/run/selfad-runner-tls','/run/runner-tls'):
 assert not os.path.exists(path),path
assert not any(key in os.environ for key in ('DOCKER_HOST','DOCKER_CERT_PATH','DOCKER_TLS_VERIFY'))
try: open('/selfad-write-test','w')
except OSError as error: assert error.errno in (errno.EROFS,errno.EACCES)
else: raise AssertionError('rootfs writable')
libc=ctypes.CDLL(None,use_errno=True)
assert libc.mount(b'none',b'/tmp',b'tmpfs',0,None)==-1 and ctypes.get_errno()==errno.EPERM
socket.create_connection((sys.argv[1],8080),2).close()
for host,port in json.loads(sys.argv[2]):
 try: socket.create_connection((host,port),0.5).close()
 except OSError: pass
 else: raise AssertionError('Forbidden network access: '+str((host,port)))
try: socket.getaddrinfo('example.com',443)
except socket.gaierror: pass
else: raise AssertionError('External DNS resolution is available')
print('PASS mount denial, no Docker credentials, positive target, cross-job/API/metadata/egress isolation')
"""

PID_PROBE = """import errno,subprocess,sys
children=[]
try:
 for i in range(100):
  try: children.append(subprocess.Popen(['/bin/sleep','10']))
  except OSError as e:
   assert e.errno==errno.EAGAIN and children; print('PASS PID enforcement'); break
 else: raise AssertionError('PID limit not enforced')
finally:
 for child in children: child.terminate()
 for child in children: child.wait()
"""


def live_build_probe(blocked):
    code = """import errno,socket,subprocess
for host,port in TARGETS:
 try: socket.create_connection((host,port),0.5).close()
 except OSError: pass
 else: raise AssertionError('Build network is not isolated')
quota,period=open('/sys/fs/cgroup/cpu.max').read().split()
assert quota!='max' and 0<int(quota)/int(period)<=2
assert 0<int(open('/sys/fs/cgroup/memory.max').read())<=4*1024**3
children=[]
try:
 for _ in range(800):
  try: children.append(subprocess.Popen(['/bin/sleep','10']))
  except OSError as error:
   assert error.errno==errno.EAGAIN and 0<len(children)<512; break
 else: raise AssertionError('Build PID cgroup limit is not inherited')
finally:
 for child in children: child.terminate()
 for child in children: child.wait()
""".replace("TARGETS", repr(blocked))
    encoded = base64.b64encode(code.encode()).decode()
    tag = "selfad-security-build-" + secrets.token_hex(5) + ":latest"
    from selfad.runtime.types import WORK_DIR

    try:
        with tempfile.TemporaryDirectory(prefix="build-security-", dir=WORK_DIR) as folder:
            Path(folder, "Dockerfile").write_text(
                f"FROM {JURY_IMAGE}\nRUN --network=default python -c 'import base64;exec(base64.b64decode(\"{encoded}\"))'\n"
            )
            result = build_image(Path(folder), tag, _label_arguments(RUNNER_LABEL))
            check(
                result.returncode == 0,
                "Live offline/resource-limited build probe failed: " + result.output[:1000],
            )
    finally:
        _docker_quiet(["image", "rm", "--force", tag])


def run_checks(control_host):
    os.environ["SELFAD_RUNNER_BUILDER"] = builder_name()
    check_api_authentication()
    verify_builder()
    runner_host = urlsplit(get_runner_settings().docker_host).hostname
    target_ips = sorted(
        {
            item[4][0]
            for host in (runner_host, control_host)
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        }
    )
    live_build_probe(
        [(ip, port) for ip in target_ips for port in (2375, 2376, 443, 8000)]
        + [("1.1.1.1", 443), ("169.254.169.254", 80)]
    )
    suffix = secrets.token_hex(5)
    networks, containers = [], []
    try:
        for index in range(2):
            name = f"selfad-security-{suffix}-{index}"
            call(
                [
                    "network",
                    "create",
                    "--internal",
                    "--ipv6=false",
                    "--opt",
                    "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                    "--opt",
                    "com.docker.network.bridge.gateway_mode_ipv6=isolated",
                    "--opt",
                    f"com.docker.network.bridge.name=sad-{suffix}{index}",
                    *_label_arguments(RUNNER_LABEL),
                    name,
                ]
            )
            networks.append(name)
        for index, network in enumerate(networks):
            name = f"selfad-security-server-{suffix}-{index}"
            containers.append(name)
            call(
                [
                    "run",
                    "--detach",
                    *options(network, name),
                    JURY_IMAGE,
                    "python",
                    "-m",
                    "http.server",
                    "8080",
                ]
            )
            for _ in range(10):
                result = _docker(
                    [
                        "exec",
                        name,
                        "python",
                        "-c",
                        "import socket;socket.create_connection(('127.0.0.1',8080),1).close()",
                    ],
                    timeout=5,
                )
                if result.returncode == 0:
                    break
            check(result.returncode == 0, "Positive-control HTTP listener failed.")
        for network in networks:
            state = json.loads(call(["network", "inspect", network]))[0]
            check(
                state["Internal"]
                and not state["EnableIPv6"]
                and state["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated",
                "Network isolation settings mismatch.",
            )
        inspected = json.loads(call(["inspect", *containers]))
        addresses = [
            entry["NetworkSettings"]["Networks"][network]["IPAddress"]
            for entry, network in zip(inspected, networks)
        ]
        for entry in inspected:
            host = entry["HostConfig"]
            check(
                host["Memory"] == 128 * 1024**2
                and host["MemorySwap"] == host["Memory"]
                and host["NanoCpus"] == 500000000
                and host["PidsLimit"] == 64
                and host["ReadonlyRootfs"]
                and not host["Privileged"]
                and entry["Config"]["User"] == "10001:10001"
                and host["CapDrop"] == ["ALL"]
                and host["PidMode"] != "host"
                and not host.get("Binds")
                and not host.get("Devices"),
                "Runtime HostConfig mismatch.",
            )
        blocked = [(ip, port) for ip in target_ips for port in (2375, 2376, 443, 8000, 8929, 5432)]
        blocked += [
            (addresses[1], 8080),
            ("1.1.1.1", 443),
            ("169.254.169.254", 80),
            ("fd00:ec2::254", 80),
        ]
        name = f"selfad-security-probe-{suffix}"
        containers.append(name)
        print(
            call(
                [
                    "run",
                    "--rm",
                    *options(networks[0], name),
                    JURY_IMAGE,
                    "python",
                    "-c",
                    RUNTIME_PROBE,
                    addresses[0],
                    json.dumps(blocked),
                ],
                timeout=60,
            ).strip()
        )
        name = f"selfad-security-oom-{suffix}"
        containers.append(name)
        result = _docker(
            [
                "run",
                *options(networks[0], name, memory="64m"),
                JURY_IMAGE,
                "python",
                "-c",
                "x=bytearray(160*1024*1024)",
            ],
            timeout=20,
        )
        state = json.loads(call(["inspect", name]))[0]["State"]
        check(
            result.returncode != 0 and state["OOMKilled"],
            "Actual cgroup OOM enforcement not observed.",
        )
        name = f"selfad-security-pids-{suffix}"
        containers.append(name)
        print(
            call(
                [
                    "run",
                    "--rm",
                    *options(networks[0], name, pids="32"),
                    JURY_IMAGE,
                    "python",
                    "-c",
                    PID_PROBE,
                ],
                timeout=20,
            ).strip()
        )
        print("PASS mTLS rejection, builder/runtime limits, network checks, OOM and PID caps")
        print(
            "NOT a proof against Docker/BuildKit/kernel vulnerabilities; keep a disposable dedicated VM."
        )
    finally:
        for name in reversed(containers):
            _docker_quiet(["rm", "--force", "--volumes", name])
        for name in reversed(networks):
            _docker_quiet(["network", "rm", name])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-host", required=True)
    run_checks(parser.parse_args().control_host)
