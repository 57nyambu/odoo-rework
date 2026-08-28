#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "Missing .env — copy .env.example to .env and fill in real values first." >&2
    exit 1
fi

set -a
source .env
set +a

envsubst < config/odoo.prod.conf.template > config/odoo.conf
echo "Wrote config/odoo.conf"
