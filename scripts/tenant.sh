#!/usr/bin/env bash
# Tenant (database-per-shop) management for the production stack.
#
#   scripts/tenant.sh create <tenant> "<Company name>" <KRA PIN> [admin-email]
#   scripts/tenant.sh upgrade <tenant> [modules]
#   scripts/tenant.sh upgrade-all [modules]
#   scripts/tenant.sh list
#   scripts/tenant.sh backup <tenant> [dir]
#   scripts/tenant.sh backup-all [dir]
#   scripts/tenant.sh drop <tenant>
#
# A tenant name is also its database name and its sub-domain:
#   https://<tenant>.$TENANT_DOMAIN
# Run from the repository root on the server; reads .env.
set -euo pipefail

cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
    echo "Missing .env (copy .env.example)." >&2
    exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

COMPOSE=(docker compose -f docker-compose.prod.yml)
MODULES_DEFAULT="${KE_MODULES:?KE_MODULES is not set in .env}"

die() { echo "ERROR: $*" >&2; exit 1; }

check_name() {
    [[ "$1" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "tenant names use 2-31 lowercase letters, digits or dashes and start with a letter: '$1'"
}

psql_db() {
    "${COMPOSE[@]}" exec -T db psql -U "$ODOO_DB_USER" -d postgres -Atc "$1"
}

tenant_exists() {
    [ "$(psql_db "SELECT 1 FROM pg_database WHERE datname = '$1'")" = "1" ]
}

list_tenants() {
    psql_db "SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn AND datname NOT IN ('postgres') ORDER BY datname"
}

odoo() {
    "${COMPOSE[@]}" exec -T odoo odoo --no-http "$@"
}

odoo_shell() {
    local db="$1"; shift
    "${COMPOSE[@]}" exec -T "$@" odoo odoo shell --no-http -d "$db" < scripts/tenant_setup.py
}

cmd_create() {
    local tenant="${1:-}" company="${2:-}" kra_pin="${3:-}" admin_email="${4:-}"
    [ -n "$tenant" ] && [ -n "$company" ] && [ -n "$kra_pin" ] || die "usage: tenant.sh create <tenant> \"<Company name>\" <KRA PIN> [admin-email]"
    check_name "$tenant"
    tenant_exists "$tenant" && die "tenant '$tenant' already exists"
    local url="https://${tenant}.${TENANT_DOMAIN}"

    echo "==> [1/4] Creating database $tenant"
    odoo -d "$tenant" -i base --stop-after-init

    echo "==> [2/4] Kenyan company settings (country, KES, timezone, KRA PIN)"
    odoo_shell "$tenant" -e SETUP_PHASE=company -e COMPANY_NAME="$company" -e KRA_PIN="$kra_pin" \
        -e ADMIN_EMAIL="$admin_email" -e BASE_URL="$url"

    echo "==> [3/4] Installing modules"
    odoo -d "$tenant" -i "$MODULES_DEFAULT" --stop-after-init

    echo "==> [4/4] Kenyan chart of accounts, VAT-inclusive prices, POS defaults"
    odoo_shell "$tenant" -e SETUP_PHASE=finalize -e BASE_URL="$url"

    echo
    echo "Tenant ready: $url"
    echo "Log in as 'admin' with the password printed above, then change it."
}

cmd_upgrade() {
    local tenant="${1:-}" modules="${2:-$MODULES_DEFAULT}"
    [ -n "$tenant" ] || die "usage: tenant.sh upgrade <tenant> [modules]"
    tenant_exists "$tenant" || die "unknown tenant '$tenant'"
    echo "==> Upgrading $tenant"
    odoo -d "$tenant" -u "$modules" --stop-after-init
}

cmd_upgrade_all() {
    local modules="${1:-$MODULES_DEFAULT}" failed=()
    for tenant in $(list_tenants); do
        if ! cmd_upgrade "$tenant" "$modules"; then
            failed+=("$tenant")
        fi
    done
    if [ "${#failed[@]}" -gt 0 ]; then
        die "upgrade failed for: ${failed[*]}"
    fi
}

cmd_backup() {
    local tenant="${1:-}" dir="${2:-${BACKUP_DIR:-./backups}}"
    [ -n "$tenant" ] || die "usage: tenant.sh backup <tenant> [dir]"
    tenant_exists "$tenant" || die "unknown tenant '$tenant'"
    local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$dir"
    "${COMPOSE[@]}" exec -T db pg_dump -U "$ODOO_DB_USER" -Fc "$tenant" > "$dir/${tenant}-${stamp}.dump"
    "${COMPOSE[@]}" exec -T odoo sh -c "test -d /var/lib/odoo/filestore/$tenant && tar -czf - -C /var/lib/odoo/filestore/$tenant . || true" \
        > "$dir/${tenant}-filestore-${stamp}.tar.gz"
    echo "Backed up $tenant -> $dir (${stamp})"
}

cmd_backup_all() {
    local dir="${1:-${BACKUP_DIR:-./backups}}"
    for tenant in $(list_tenants); do
        cmd_backup "$tenant" "$dir"
    done
}

cmd_drop() {
    local tenant="${1:-}"
    [ -n "$tenant" ] || die "usage: tenant.sh drop <tenant>"
    tenant_exists "$tenant" || die "unknown tenant '$tenant'"
    read -r -p "Type the tenant name to permanently delete it (take a backup first): " answer
    [ "$answer" = "$tenant" ] || die "aborted"
    psql_db "DROP DATABASE \"$tenant\""
    "${COMPOSE[@]}" exec -T odoo rm -rf "/var/lib/odoo/filestore/$tenant"
    echo "Dropped $tenant"
}

case "${1:-}" in
    create) shift; cmd_create "$@" ;;
    upgrade) shift; cmd_upgrade "$@" ;;
    upgrade-all) shift; cmd_upgrade_all "$@" ;;
    list) list_tenants ;;
    backup) shift; cmd_backup "$@" ;;
    backup-all) shift; cmd_backup_all "$@" ;;
    drop) shift; cmd_drop "$@" ;;
    *) sed -n '2,15p' "$0"; exit 1 ;;
esac
