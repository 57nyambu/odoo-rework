# Odoo Kenya ERP/POS — README

Everything referenced below is in this folder — a working, syntax-checked repo skeleton (Python compiles, XML is well-formed, YAML parses, manifests load). Unzip it and follow this top to bottom. It won't run a full checkout end-to-end on its own — the eTIMS connector and a few POS-frontend details need the version-specific fixes flagged below — but every file is real, in the right place, and wired together correctly.

```
odoo-kenya/
├── docker-compose.yml              # dev
├── docker-compose.prod.yml         # prod
├── .env.example                    # copy to .env in prod, never commit .env
├── config/
│   ├── odoo.conf                   # dev config, used as-is
│   └── odoo.prod.conf.template     # prod config, rendered via envsubst
├── scripts/
│   ├── render-config.sh            # .env + template → config/odoo.conf
│   └── backup.sh                   # pg_dump + filestore tar, for cron
├── deploy/
│   ├── nginx/odoo-kenya.conf
│   └── Jenkinsfile
├── addons-oca/                     # empty — git-submodule OCA modules here
└── addons-custom/
    ├── ke_base/                    # phone normalization, shared helpers
    ├── ke_etims/                   # swappable eTIMS interface
    ├── ke_mpesa/                   # Daraja STK Push + webhook + POS payment method
    ├── ke_pos_ui/                  # branding, receipt template, checkout patches
    ├── ke_cash_purchase/           # one-step cash purchase wizard
    ├── ke_uac/                     # custom groups + record rules
    └── ke_reporting/               # daily sales/VAT dashboard
```

---

## Before you start: three things that need a decision or a version check

1. **eTIMS connector.** `ke_etims/models/etims_service.py` defines the interface but deliberately raises `UserError` — there's no real KRA connector wired in. Per the execution plan, buy a vetted OSCU/VSCU connector module and add a second module that depends on `ke_etims` and overrides `sign_and_submit`. Don't skip this before going live; it's the one piece that's genuinely a placeholder.
2. **POS frontend internals.** `ke_mpesa/static/src/js/payment_mpesa.js` and `ke_pos_ui/static/src/js/pos_ui_patch.js` both use OWL import paths and method names (`PaymentInterface`, `send_payment_request`, `ProductScreen.addProductToOrder`) that shift between Odoo point releases more than anything else in the codebase. Before relying on them, run:
   ```bash
   grep -rn "class PaymentInterface" /path/to/odoo/addons/point_of_sale/static/src/
   grep -rn "send_payment_request" /path/to/odoo/addons/point_of_sale/static/src/
   ```
   against your actual installed Odoo 19 source and fix the import paths/method names to match. The files are marked with `CAUTION` comments at exactly this spot.
3. **Stock quantity field name.** `ke_cash_purchase/models/cash_purchase_wizard.py` sets `move.quantity_done` — this field has been renamed across Odoo versions (`qty_done` → `quantity_done` → `quantity` depending on release). If module install or the wizard throws an `AttributeError` there, `grep -rn "qty_done\|quantity_done" addons/stock/models/stock_move.py` in your installed source and fix the one line.

Everything else below is either standard Odoo (won't have shifted) or infrastructure you control directly (nginx, Docker, Jenkins).

---

## Part 1 — Local setup

### 1.1 Prerequisites

| Component | Version |
|---|---|
| Docker + Compose | any recent stable |
| Git | any |

You don't need Python, PostgreSQL, or Node installed on the host — the dev `docker-compose.yml` runs Odoo and Postgres in containers. You'll only need Python/Node locally if you start developing the JS/CSS assets outside a container.

### 1.2 Get the scaffold running

```bash
# 1. Unzip and enter the project
unzip odoo-kenya-scaffold.zip
cd odoo-kenya

# 2. Turn this into its own git repo — addons-custom is what your Jenkins
#    pipeline will pull in production, so it needs its own history from day one
git init
git add .
git commit -m "Initial scaffold: dev/prod compose, ke_* addon skeletons"

# 3. Start Postgres + Odoo
docker compose up -d

# 4. Watch the logs until Odoo reports it's listening on 8069
docker compose logs -f odoo
```

### 1.3 Create the working database with Kenya localization

```bash
docker compose exec odoo odoo \
    -d odoo_kenya --without-demo=all --stop-after-init \
    -i base
```

Then open `http://localhost:8069`, log in as `admin` (Odoo will have prompted you to set the master password / create the db if you skip the CLI step above — either path works, but the CLI path is faster and reproducible).

**Before installing anything accounting-related:** go to **Settings → General Settings**, set the company **Country** to **Kenya**. This is what makes `l10n_ke` auto-install as the fiscal localization when you install Invoicing. Installing Invoicing first with the wrong country set loads the generic chart of accounts, and re-doing that later is a real headache — don't skip the order here.

### 1.4 Install the core apps (in this order)

From the Apps menu (or `-i` on the CLI, comma-separated), install:

```
contacts, stock, sale, sale_stock, purchase, purchase_stock, account, point_of_sale, stock_barcode
```

CLI equivalent:
```bash
docker compose exec odoo odoo \
    -d odoo_kenya --stop-after-init \
    -i contacts,stock,sale,sale_stock,purchase,purchase_stock,account,point_of_sale,stock_barcode
```

### 1.5 Install the custom addons

They're already mounted into the container at `/mnt/extra-addons/custom` via `docker-compose.yml`. Install in dependency order (Odoo will actually resolve this for you via `depends`, but explicit is easier to debug if something fails):

```bash
docker compose exec odoo odoo \
    -d odoo_kenya --stop-after-init \
    -i ke_base,ke_etims,ke_mpesa,ke_pos_ui,ke_cash_purchase,ke_uac,ke_reporting
```

If `ke_etims` fails to install because `l10n_ke` isn't present, it means the company country wasn't set to Kenya before this step — go back to §1.3.

### 1.6 Iterating on custom module code

Restart isn't needed for most changes — Odoo can hot-reload with `-u` (update) while the container keeps running:

```bash
# after editing a model, view, or manifest in ke_mpesa:
docker compose exec odoo odoo -d odoo_kenya -u ke_mpesa --stop-after-init
docker compose restart odoo
```

For pure JS/CSS changes (POS frontend assets), a hard-refresh in the browser is often enough without a module update — Odoo serves assets from disk in dev mode. If it doesn't pick up, add `?debug=assets` to the POS URL to force asset recompilation.

### 1.7 Configuring M-Pesa for local testing

In the Odoo backend: **Point of Sale → Configuration → Payment Methods**, create one with `ke_is_mpesa` checked, environment set to **Sandbox**, and your Daraja sandbox consumer key/secret/shortcode/passkey (get these from the [Safaricom Developer Portal](https://developer.safaricom.co.ke) — same sandbox credentials pattern you've used before). The callback URL Daraja needs is `http://<your-ngrok-or-tunnel-url>/ke_mpesa/callback` — Daraja can't reach `localhost` directly, so tunnel with ngrok or similar for local STK Push testing.

---

## Part 2 — Production setup

Target: the existing Contabo VPS (`vmi3055286`), single user `tom`, same conventions as your other apps.

### 2.1 Provision the app directory

```bash
ssh tom@<host>
mkdir -p /home/tom/apps/odoo-kenya
cd /home/tom/apps/odoo-kenya
git clone <your-addons-custom-remote-or-full-repo-url> .
```

### 2.2 Set up secrets

```bash
cp .env.example .env
nano .env   # fill in real ODOO_DB_PASSWORD, ODOO_ADMIN_PASSWD, etc.
```

`.env` should already be in `.gitignore` — double-check before your first commit from this checkout, since it's easy to accidentally commit it from a fresh clone.

### 2.3 Render the production config

```bash
# needs gettext-base for envsubst — install if missing:
sudo apt-get install -y gettext-base

bash scripts/render-config.sh
```

This reads `.env` + `config/odoo.prod.conf.template` and writes `config/odoo.conf` — the actual file Docker mounts. Re-run this any time you change `.env`.

### 2.4 First boot in production

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec odoo odoo \
    -d "$ODOO_DB_NAME" --without-demo=all --stop-after-init -i base
```

Set the company country to Kenya (§1.3) before installing accounting, same as dev. Then install the same app list as §1.4 and §1.5, swapping `docker compose` for `docker compose -f docker-compose.prod.yml`.

### 2.5 nginx

```bash
sudo cp deploy/nginx/odoo-kenya.conf /etc/nginx/sites-available/odoo-kenya
sudo ln -s /etc/nginx/sites-available/odoo-kenya /etc/nginx/sites-enabled/odoo-kenya
sudo nginx -t
```

Edit `server_name` in that file to your real domain before the `nginx -t` check — it'll still pass with the placeholder, but won't route real traffic.

### 2.6 SSL

```bash
sudo certbot --nginx -d erp.yourdomain.com
```

Certbot rewrites the `443` server block to add `ssl_certificate`/`ssl_certificate_key` lines automatically — don't hand-add them first, and don't re-run `render-config.sh` or re-copy the nginx file afterward without diffing, or you'll clobber Certbot's edits.

```bash
sudo systemctl reload nginx
```

### 2.7 First deploy via Jenkins

Point a new Jenkins pipeline job at `deploy/Jenkinsfile`. It assumes:
- a credential named `motorgarage-ssh` (SSH private key, authenticates as `tom`) — reuse the existing one
- a credential named `sv2-host` (secret text, the deploy host) — reuse or create to match your convention

Check the exact credential-binding syntax against one of your existing Jenkinsfiles before the first run — this one mirrors the pattern (same SSH key, same `-i "$SSH_KEY" $SSH_OPTS` word-splitting guard, same route-agnostic `curl -s` health check) but wasn't copied from a specific working file, so the Groovy credential bindings are worth a quick sanity check.

### 2.8 Subsequent deploys

Same pipeline, re-run. It pulls `addons-custom`, re-renders config, restarts containers, runs `-u` on all seven custom modules, and health-checks `/web/login`. For a manual deploy without Jenkins:

```bash
cd /home/tom/apps/odoo-kenya
git pull origin main
bash scripts/render-config.sh
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec -T odoo \
    odoo -u ke_base,ke_etims,ke_mpesa,ke_pos_ui,ke_cash_purchase,ke_uac,ke_reporting \
    -d "$ODOO_DB_NAME" --stop-after-init
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login
```

### 2.9 Backups

Wire `scripts/backup.sh` into your existing `db-backup-service` cron job rather than running it standalone — it needs `ODOO_DB_NAME`/`ODOO_DB_USER` sourced from `.env` first:

```bash
set -a; source /home/tom/apps/odoo-kenya/.env; set +a
bash /home/tom/apps/odoo-kenya/scripts/backup.sh /home/tom/backups/odoo-kenya
```

Then let the existing GFS rotation + rclone-to-Contabo-S3 logic pick up the dated `.sql` and `.tar.gz` files from that output directory, same as your other apps.

### 2.10 Permissions

Same rule as everywhere else on this box: any permission issue on `/home/tom/apps/odoo-kenya` is `sudo chown -R tom:tom /home/tom/apps/odoo-kenya`. No per-app user.

---

## Common operations cheatsheet

| Task | Command |
|---|---|
| Tail Odoo logs (dev) | `docker compose logs -f odoo` |
| Tail Odoo logs (prod) | `docker compose -f docker-compose.prod.yml exec odoo tail -f /var/log/odoo/odoo.log` |
| Odoo shell (inspect/debug records) | `docker compose exec odoo odoo shell -d odoo_kenya` |
| Upgrade one module | `docker compose exec odoo odoo -u ke_mpesa -d odoo_kenya --stop-after-init` |
| Restart just the app container | `docker compose restart odoo` |
| Drop and recreate the dev DB | `docker compose exec odoo odoo -d odoo_kenya --stop-after-init -i base` after dropping via `dropdb` in the `db` container |
| Restore a backup locally | `docker compose exec -T db psql -U odoo -d odoo_kenya < backup.sql` |

## Troubleshooting

- **Module fails to install with a missing-dependency error on `l10n_ke`** — company country isn't set to Kenya yet. Fix in Settings, then retry the `-i`/`-u`.
- **`AttributeError` on `quantity_done`** — see the caveat in §"Before you start", item 3.
- **POS payment screen doesn't show M-Pesa, or throws a JS console error on `PaymentInterface`** — see item 2; the OWL import path in `payment_mpesa.js` needs to match your installed version.
- **eTIMS submission raises "No eTIMS connector is configured"** — expected until you install a connector module that overrides `ke.etims.service.sign_and_submit`. This isn't a bug, it's the interface doing its job.
- **Daraja callback never arrives locally** — `localhost` isn't reachable from Safaricom's servers; you need a tunnel (ngrok, cloudflared) for local STK Push testing, and the tunnel URL has to be set as the payment method's callback base or matched by `web.base.url` in Odoo's system parameters.
