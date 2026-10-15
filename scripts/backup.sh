#!/usr/bin/env bash
# Backs up every tenant database (+ filestore). Meant to be called from the
# existing db-backup-service cron job alongside the other apps:
#
#   bash /home/tom/apps/odoo-kenya/scripts/backup.sh /home/tom/backups/odoo-kenya
#
# Pass a tenant name as second argument to back up a single tenant.
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${1:-/home/tom/backups/odoo-kenya}"

if [ -n "${2:-}" ]; then
    exec bash scripts/tenant.sh backup "$2" "$BACKUP_DIR"
fi
exec bash scripts/tenant.sh backup-all "$BACKUP_DIR"
