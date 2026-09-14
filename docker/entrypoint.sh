#!/bin/sh

set -eu

state_dir="/data/selfad"
data_dir="${SELFAD_DATA_DIR:-${state_dir}/data}"
secrets_dir="${state_dir}/secrets"
password_file="${secrets_dir}/gitea_admin_password"
legacy_password_file="${secrets_dir}/gitlab_root_password"

install -d -m 0755 -o root -g root "${state_dir}"
install -d -m 0750 -o selfad -g selfad "${data_dir}"
install -d -m 0750 -o root -g selfad "${secrets_dir}"

if [ ! -s "${password_file}" ]; then
    umask 077
    if [ -s "${legacy_password_file}" ]; then
        cp "${legacy_password_file}" "${password_file}"
    else
        python3 -c 'import secrets; print(secrets.token_urlsafe(36))' \
            > "${password_file}"
    fi
fi
chown root:selfad "${password_file}"
chmod 0440 "${password_file}"

echo "SelfAD and Gitea will run inside this container."
echo "SelfAD: http://localhost:8000"
echo "Gitea: ${SELFAD_GITEA_PUBLIC_URL:-http://localhost:8929}"
echo "Gitea login: root"
echo "Gitea password: docker exec <container> cat ${password_file}"

exec /usr/bin/entrypoint "$@"
