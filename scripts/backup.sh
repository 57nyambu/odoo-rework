#!/usr/bin/env bash
# Meant to be called from the existing db-backup-service cron job alongside
# your other apps, not run standalone. Adjust DB_NAME / paths to match your
# actual db-backup-service conventions before wiring this in.
set -euo pipefail

DB_NAME="${ODOO_DB_NAME:-odoo_kenya_prod}"
BACKUP_DIR="${1:-/home/tom/backups/odoo-kenya}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

docker compose -f docker-compose.prod.yml exec -T db \
    pg_dump -U "${ODOO_DB_USER:-odoo}" "$DB_NAME" \
    > "$BACKUP_DIR/${DB_NAME}-${TIMESTAMP}.sql"

# Filestore lives on a named volume, not bind-mounted, so we tar it from
# inside the container rather than assuming a host path.
docker compose -f docker-compose.prod.yml exec -T odoo \
    tar -czf - -C /var/lib/odoo/filestore . \
    > "$BACKUP_DIR/${DB_NAME}-filestore-${TIMESTAMP}.tar.gz"

echo "Backed up $DB_NAME to $BACKUP_DIR (db + filestore, ${TIMESTAMP})"
