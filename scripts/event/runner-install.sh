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
data_root=${SELFAD_RUNNER_DATA_ROOT:-/var/lib/docker}
case "$data_root" in /var/lib/docker|/srv/*) ;; *) echo "Use /var/lib/docker or a dedicated /srv/... filesystem." >&2; exit 64;; esac
if ! mountpoint -q "$data_root"; then
    echo "Refusing shared OS disk: mount a dedicated Docker filesystem at $data_root first." >&2
    exit 69
fi
python3 -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); assert str(p.resolve())==str(p) and p.stat().st_dev!=pathlib.Path("/").stat().st_dev, "Docker data must use a separate filesystem, not a bind mount of the OS filesystem"' "$data_root"
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
install -m 0755 "$(dirname "$0")/runner-firewall.sh" /usr/local/sbin/selfad-runner-firewall
/usr/local/sbin/selfad-runner-firewall "$control_plane_ip"
cat > /etc/systemd/system/selfad-runner-firewall.service <<EOF
[Unit]
Description=SelfAD dedicated runner API and job-network firewall
Before=docker.service
After=network-pre.target ufw.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/selfad-runner-firewall $control_plane_ip
[Install]
WantedBy=multi-user.target
EOF
systemctl enable selfad-runner-firewall.service
install -m 0600 "$tls_source/key.pem" "$tls_target/key.pem"
install -m 0644 "$tls_source/ca.pem" "$tls_target/ca.pem"
install -m 0644 "$tls_source/cert.pem" "$tls_target/cert.pem"

cat > "$override_file" <<EOF
[Unit]
Requires=selfad-runner-firewall.service
After=selfad-runner-firewall.service
[Service]
ExecStart=
ExecStart=$dockerd_path -H fd:// -H tcp://0.0.0.0:2376 --data-root=$data_root --feature containerd-snapshotter=false --tlsverify --tlscacert=/etc/docker/selfad-tls/ca.pem --tlscert=/etc/docker/selfad-tls/cert.pem --tlskey=/etc/docker/selfad-tls/key.pem --userns-remap=default --icc=false --live-restore --default-cgroupns-mode=private --log-driver=local --log-opt max-size=5m --log-opt max-file=2
EOF

systemctl daemon-reload
systemctl restart docker
systemctl is-active --quiet docker

printf '%s\n' "Runner Docker API is listening with mutual TLS."
printf '%s\n' "Verify only from the control plane using the control-plane TLS bundle."
