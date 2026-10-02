FROM docker.gitea.com/gitea:1.27.3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SELFAD_DATA_DIR=/data/selfad/data \
    SELFAD_GITEA_INTERNAL_URL=http://127.0.0.1:8929 \
    SELFAD_GITEA_PUBLIC_URL=http://localhost:8929 \
    SELFAD_GITEA_TOKEN_FILE=/data/selfad/data/gitea_token \
    SELFAD_GITEA_PASSWORD_FILE=/data/selfad/secrets/gitea_admin_password \
    SELFAD_GITEA_VERIFY_TLS=false \
    SELFAD_RUNNER_DOCKER_HOST=unix:///run/selfad-docker/docker.sock \
    SELFAD_ENABLE_INTERNAL_RUNNER=false \
    SSH_MAX_STARTUPS=100:30:200 \
    SSH_INCLUDE_FILE=/etc/ssh/selfad.conf \
    GITEA__server__DOMAIN=localhost \
    GITEA__server__HTTP_PORT=8929 \
    GITEA__server__ROOT_URL=http://localhost:8929/ \
    GITEA__server__SSH_DOMAIN=localhost \
    GITEA__server__SSH_PORT=2224 \
    GITEA__security__INSTALL_LOCK=true \
    GITEA__service__DISABLE_REGISTRATION=true \
    GITEA__service__REQUIRE_SIGNIN_VIEW=true \
    GITEA__service__ENABLE_CAPTCHA=true \
    GITEA__service__REQUIRE_CAPTCHA_FOR_LOGIN=true \
    GITEA__service__CAPTCHA_TYPE=image \
    GITEA__repository__FORCE_PRIVATE=true \
    GITEA__repository__MAX_CREATION_LIMIT=0 \
    GITEA__repository__DISABLE_HTTP_GIT=true \
    GITEA__repository__DISABLE_MIGRATIONS=true \
    GITEA__repository__upload__ENABLED=false \
    GITEA__attachment__ENABLED=false \
    GITEA__packages__ENABLED=false \
    GITEA__webhook__ALLOWED_HOST_LIST=loopback \
    GITEA__actions__ENABLED=false

USER root

RUN apk add --no-cache python3 py3-pip docker tzdata \
    && python3 -m venv /opt/selfad/venv \
    && addgroup -S -g 10001 selfad \
    && adduser \
        -S \
        -D \
        -H \
        -u 10001 \
        -G selfad \
        -s /sbin/nologin \
        selfad \
    && addgroup selfad docker

WORKDIR /app

COPY requirements.txt .
RUN /opt/selfad/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY main.py .
COPY selfad ./selfad
COPY docker/entrypoint.sh /opt/selfad/entrypoint.sh
COPY docker/selfad-run /etc/s6/selfad/run
COPY docker/dockerd-run /etc/s6/dockerd/run
COPY docker/healthcheck.sh /opt/selfad/healthcheck.sh
COPY docker/sshd_config /etc/ssh/selfad.conf

RUN chmod 0755 \
        /opt/selfad/entrypoint.sh \
        /opt/selfad/healthcheck.sh \
        /etc/s6/selfad/run \
        /etc/s6/dockerd/run \
    && chmod 0644 /etc/ssh/selfad.conf \
    && chown -R root:root /app /opt/selfad /etc/s6/selfad

EXPOSE 8000 8929 22

HEALTHCHECK --interval=20s --timeout=5s --start-period=2m --retries=5 \
    CMD /opt/selfad/healthcheck.sh

ENTRYPOINT ["/opt/selfad/entrypoint.sh"]
