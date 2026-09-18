#!/bin/sh

# Install the SelfAD Docker executor on a fresh, dedicated runner VM.
# It deliberately refuses a host that already runs containers.
set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <runner-tls-directory> <control-plane-ipv4>" >&2
    exit 64
fi

tls_source=$1
control_plane_ip=$2
tls_target=/etc/docker/selfad-tls
override_dir=/etc/systemd/system/docker.service.d
override_file="$override_dir/selfad-runner.conf"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root on the dedicated runner VM." >&2
    exit 77
fi
case "$control_plane_ip" in
    *[!0-9.]*|.*|*..*|*.)
        echo "Control-plane address must be an IPv4 address." >&2
        exit 64
        ;;
esac
for required_file in ca.pem cert.pem key.pem; do
    if [ ! -f "$tls_source/$required_file" ]; then
        echo "Missing $tls_source/$required_file" >&2
        exit 66
    fi
done
if ! command -v systemctl >/dev/null || ! command -v dockerd >/dev/null; then
    echo "Docker Engine and systemd must be installed before running this script." >&2
    exit 69
fi
dockerd_path=$(command -v dockerd)
if docker ps -aq 2>/dev/null | grep -q .; then
    echo "Refusing a runner host that already has Docker containers." >&2
    exit 73
fi
if [ -e "$override_file" ] && [ "${SELFAD_RUNNER_REPLACE:-false}" != "true" ]; then
    echo "Refusing to replace existing $override_file; set SELFAD_RUNNER_REPLACE=true after review." >&2
    exit 73
fi

# An active UFW installation can be changed safely and specifically. On cloud
# firewalls or nftables, the operator must attest that the equivalent rule is
# already present before exposing the Docker API.
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow from "$control_plane_ip" to any port 2376 proto tcp
elif [ "${SELFAD_RUNNER_FIREWALL_CONFIRMED:-false}" != "true" ]; then
    echo "No active UFW firewall found. Allow only $control_plane_ip/tcp:2376 in the external firewall, then rerun with SELFAD_RUNNER_FIREWALL_CONFIRMED=true." >&2
    exit 69
fi

install -d -m 0700 "$tls_target" "$override_dir"
install -m 0600 "$tls_source/key.pem" "$tls_target/key.pem"
install -m 0644 "$tls_source/ca.pem" "$tls_target/ca.pem"
install -m 0644 "$tls_source/cert.pem" "$tls_target/cert.pem"

cat > "$override_file" <<EOF
[Service]
ExecStart=
ExecStart=$dockerd_path -H fd:// -H tcp://0.0.0.0:2376 --tlsverify --tlscacert=/etc/docker/selfad-tls/ca.pem --tlscert=/etc/docker/selfad-tls/cert.pem --tlskey=/etc/docker/selfad-tls/key.pem --userns-remap=default --icc=false --live-restore
EOF

systemctl daemon-reload
systemctl restart docker
systemctl is-active --quiet docker

printf '%s\n' "Runner Docker API is listening with mutual TLS."
printf '%s\n' "Verify only from the control plane using the control-plane TLS bundle."
