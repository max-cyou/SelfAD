#!/bin/sh

set -eu

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <new-output-directory> <runner-dns-or-ip> <control-plane-name>" >&2
    exit 64
fi

output_dir=$1
runner_endpoint=$2
client_name=$3
key_bits=${SELFAD_TLS_KEY_BITS:-4096}

case "$output_dir" in
    /*) ;;
    *) echo "Output directory must be an absolute path." >&2; exit 64 ;;
esac
case "$runner_endpoint" in
    ''|*[!A-Za-z0-9.-]*) echo "Runner DNS name or IP is invalid." >&2; exit 64 ;;
esac
case "$client_name" in
    ''|*[!A-Za-z0-9_.-]*) echo "Control-plane name is invalid." >&2; exit 64 ;;
esac
case "$key_bits" in
    2048|3072|4096) ;;
    *) echo "SELFAD_TLS_KEY_BITS must be 2048, 3072 or 4096." >&2; exit 64 ;;
esac
if [ -e "$output_dir" ]; then
    echo "Refusing to write into an existing path: $output_dir" >&2
    exit 73
fi

case "$runner_endpoint" in
    *[!0-9.]*) subject_alt_name="DNS:$runner_endpoint" ;;
    *) subject_alt_name="IP:$runner_endpoint" ;;
esac

umask 077
mkdir -p "$output_dir/runner" "$output_dir/control-plane"

openssl req -x509 -new -nodes -newkey "rsa:$key_bits" \
    -keyout "$output_dir/ca-key.pem" \
    -out "$output_dir/ca.pem" \
    -days 365 \
    -sha256 \
    -subj "/CN=SelfAD runner CA" >/dev/null 2>&1

openssl req -new -nodes -newkey "rsa:$key_bits" \
    -keyout "$output_dir/runner/key.pem" \
    -out "$output_dir/runner/request.csr" \
    -subj "/CN=$runner_endpoint" >/dev/null 2>&1
printf '%s\n' \
    'basicConstraints=critical,CA:FALSE' \
    'keyUsage=critical,digitalSignature,keyEncipherment' \
    'extendedKeyUsage=serverAuth' \
    "subjectAltName=$subject_alt_name" > "$output_dir/runner/extensions.cnf"
openssl x509 -req \
    -in "$output_dir/runner/request.csr" \
    -CA "$output_dir/ca.pem" \
    -CAkey "$output_dir/ca-key.pem" \
    -CAcreateserial \
    -out "$output_dir/runner/cert.pem" \
    -days 365 \
    -sha256 \
    -extfile "$output_dir/runner/extensions.cnf" >/dev/null 2>&1

openssl req -new -nodes -newkey "rsa:$key_bits" \
    -keyout "$output_dir/control-plane/key.pem" \
    -out "$output_dir/control-plane/request.csr" \
    -subj "/CN=$client_name" >/dev/null 2>&1
printf '%s\n' \
    'basicConstraints=critical,CA:FALSE' \
    'keyUsage=critical,digitalSignature,keyEncipherment' \
    'extendedKeyUsage=clientAuth' > "$output_dir/control-plane/extensions.cnf"
openssl x509 -req \
    -in "$output_dir/control-plane/request.csr" \
    -CA "$output_dir/ca.pem" \
    -CAkey "$output_dir/ca-key.pem" \
    -CAcreateserial \
    -out "$output_dir/control-plane/cert.pem" \
    -days 365 \
    -sha256 \
    -extfile "$output_dir/control-plane/extensions.cnf" >/dev/null 2>&1

cp "$output_dir/ca.pem" "$output_dir/runner/ca.pem"
cp "$output_dir/ca.pem" "$output_dir/control-plane/ca.pem"
rm -f "$output_dir/runner/request.csr" \
    "$output_dir/runner/extensions.cnf" \
    "$output_dir/control-plane/request.csr" \
    "$output_dir/control-plane/extensions.cnf" \
    "$output_dir/ca.srl"
chmod 0600 \
    "$output_dir/ca-key.pem" \
    "$output_dir/runner/key.pem" \
    "$output_dir/control-plane/key.pem"
chmod 0644 \
    "$output_dir/ca.pem" \
    "$output_dir/runner/ca.pem" \
    "$output_dir/runner/cert.pem" \
    "$output_dir/control-plane/ca.pem" \
    "$output_dir/control-plane/cert.pem"

printf '%s\n' "Created runner TLS material in $output_dir"
printf '%s\n' "- copy $output_dir/runner to /etc/docker/selfad-tls on the runner VM"
printf '%s\n' "- copy only $output_dir/control-plane to the control-plane host"
printf '%s\n' "- store $output_dir/ca-key.pem offline, then remove it from the VM"
