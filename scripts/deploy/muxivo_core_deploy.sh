#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${ARCHIVE:-/tmp/muxivo-core-release.tar.gz}"
MODEL_ARCHIVE="${MODEL_ARCHIVE:-}"
ROOT_TMP="${ROOT_TMP:-/root/tmp}"
APP_DIR="${APP_DIR:-/opt/muxivo-core}"
BACKUP_DIR="${BACKUP_DIR:-/opt}"
BACKUP="$BACKUP_DIR/muxivo-core.backup-$(date +%Y%m%d%H%M%S).tgz"
ROOT_ARCHIVE="$ROOT_TMP/muxivo-core-release.tar.gz"
ENV_FILE="${ENV_FILE:-}"
SERVICES=(${SERVICES:-muxivo-core.service})

log() {
    printf '[muxivo-core-deploy] %s\n' "$*"
}

mkdir -p "$ROOT_TMP"
id -u muxivo-core >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/muxivo-core --shell /usr/sbin/nologin muxivo-core

log "Copy archive to root tmp..."
cp "$ARCHIVE" "$ROOT_ARCHIVE"

if [ -d "$APP_DIR" ]; then
    log "Create backup..."
    APP_NAME="$(basename "$APP_DIR")"
    tar \
        --exclude="$APP_NAME/.venv" \
        --exclude="$APP_NAME/models" \
        --exclude="$APP_NAME/logs" \
        --exclude="$APP_NAME/__pycache__" \
        --exclude="$APP_NAME/.pytest_cache" \
        -C "$(dirname "$APP_DIR")" \
        -czf "$BACKUP" \
        "$APP_NAME"
else
    log "App directory does not exist yet, backup skipped."
fi

log "Stop services..."
for service in "${SERVICES[@]}"; do
    systemctl stop "$service" || true
done

log "Extract new files over existing project..."
mkdir -p "$APP_DIR"
tar -xzf "$ROOT_ARCHIVE" -C "$APP_DIR"

if [ -n "$MODEL_ARCHIVE" ]; then
    log "Extract trained model..."
    mkdir -p "$APP_DIR/models"
    tar -xzf "$MODEL_ARCHIVE" -C "$APP_DIR/models"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
install -o root -g root -m 644 "$APP_DIR/scripts/deploy/muxivo-core.service" /etc/systemd/system/muxivo-core.service
mkdir -p "$APP_DIR/logs"
chown -R muxivo-core:muxivo-core "$APP_DIR"
systemctl daemon-reload

log "Start services..."
for service in "${SERVICES[@]}"; do
    systemctl start "$service"
done

log "Status:"
for service in "${SERVICES[@]}"; do
    systemctl is-active "$service"
done

log "Backup created: ${BACKUP:-none}"
log "Done"
