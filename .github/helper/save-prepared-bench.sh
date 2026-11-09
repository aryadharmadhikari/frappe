#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$SNAPSHOT_DIR"
curl --fail --silent --show-error --retry 30 --retry-delay 1 --retry-connrefused \
  --max-time 5 http://test_site:8000/api/method/ping > /dev/null
bench --site test_site backup --ignore-backup-conf \
  --backup-path-db "$SNAPSHOT_DIR/database.sql.gz"
printf '%s\n' "$GITHUB_WORKSPACE" > "$SNAPSHOT_DIR/workspace"
printf '%s\n' "$DB" > "$SNAPSHOT_DIR/db"
lsb_release -rs > "$SNAPSHOT_DIR/ubuntu-version"
python -c 'import platform; print(platform.python_version())' > "$SNAPSHOT_DIR/python-version"
node -p 'process.versions.node' > "$SNAPSHOT_DIR/node-version"
tar --exclude='sites/*/logs' --exclude='sites/*/private/backups' \
  --exclude='config/pids' --exclude='config/*.pid' -cf "$SNAPSHOT_DIR/bench.tar" \
  apps env sites config Procfile chromium
