# Odoo Kenya ERP / POS

Odoo 19 Community, adapted to the Kenyan market and packaged for
multi-tenant hosting:

- **Point of Sale for Kenya** - M-Pesa STK push and "verify M-Pesa code"
  (customer paid to the till), KRA eTIMS signed receipts with QR code,
  KRA PIN on customers, VAT-inclusive pricing (16% / 8% / 0% / exempt),
  KES 1.00 rounding, branch restrictions for cashiers.
- **KRA eTIMS layer** for invoices, credit notes and POS receipts: queue
  with retries, never blocks a sale, pluggable OSCU/VSCU connector.
- **M-Pesa (Safaricom Daraja)**: STK push, status queries, C2B callbacks,
  anti double-use, Data Protection Act safeguards.
- **E-commerce Exchange API**: REST + signed webhooks so an online shop
  shares the catalog, branch stock, orders, payments, deliveries and
  eTIMS invoices with Odoo.
- **Multi-tenancy**: one database per business behind its own sub-domain,
  plus Odoo multi-company inside a tenant; provisioning/upgrade/backup tooling.

Everything Kenyan lives in `addons-custom/ke_*`; standard Odoo is extended,
not forked. The modules are installed and tested on Odoo 19.0 (59 automated
tests, see [Tests](#tests)).

## Documentation

| Document | For |
|---|---|
| [docs/POS_USER_GUIDE.md](docs/POS_USER_GUIDE.md) | Shop owners, managers, cashiers: setup and daily use of the POS, M-Pesa, eTIMS, reports. |
| [docs/MPESA_SETUP.md](docs/MPESA_SETUP.md) | Connecting a till/paybill (Daraja sandbox → production), callbacks, troubleshooting. |
| [docs/ETIMS.md](docs/ETIMS.md) | eTIMS behaviour, configuration, writing an OSCU/VSCU connector, go-live checklist. |
| [docs/ECOMMERCE_API.md](docs/ECOMMERCE_API.md) | API reference: authentication, endpoints, errors, webhooks. |
| [docs/ECOMMERCE_INTEGRATION_GUIDE.md](docs/ECOMMERCE_INTEGRATION_GUIDE.md) | Step-by-step integration for shop developers, with Node.js / PHP / Python code. |
| [addons-custom/ke_ecommerce_api/static/api/openapi.yaml](addons-custom/ke_ecommerce_api/static/api/openapi.yaml) | OpenAPI 3.1 spec (Postman, Swagger, client generators). |
| [docs/MULTI_TENANCY.md](docs/MULTI_TENANCY.md) | Operators: DNS/TLS, creating and upgrading tenants, backups, isolation. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Developers: module map, flows, security model, Odoo 19 notes. |

## Repository layout

```
odoo-kenya/
├── docker-compose.yml              # dev (Odoo 19 + PostgreSQL 16)
├── docker-compose.prod.yml         # prod
├── .env.example                    # copy to .env in prod, never commit .env
├── config/
│   ├── odoo.conf                   # dev config
│   └── odoo.prod.conf.template     # prod config (dbfilter per sub-domain), rendered from .env
├── scripts/
│   ├── render-config.sh            # .env + template → config/odoo.conf
│   ├── tenant.sh                   # create / list / upgrade / backup / drop tenants
│   ├── tenant_setup.py             # Kenyan company + chart of accounts setup (run by tenant.sh)
│   └── backup.sh                   # backup all tenants (cron)
├── deploy/
│   ├── nginx/odoo-kenya.conf       # wildcard sub-domains, websocket, API rate limit
│   └── Jenkinsfile                 # checks, backup + upgrade all tenants, health check
├── docs/                           # see table above
├── addons-oca/                     # optional OCA modules (git submodules)
└── addons-custom/
    ├── ke_base/                    # KRA PIN, phone formats, Data Protection helpers
    ├── ke_etims/                   # eTIMS gateway, queue, tax types, invoice QR
    ├── ke_mpesa/                   # Daraja client, STK push, C2B callbacks
    ├── ke_pos/                     # Kenyan POS (M-Pesa terminal, eTIMS receipts)
    ├── ke_ecommerce_api/           # REST API + webhooks for online shops
    ├── ke_cash_purchase/           # one-step cash purchases (KRA input-VAT rule)
    ├── ke_uac/                     # retail roles, per-cashier shop restriction
    └── ke_reporting/               # daily collections, VAT by eTIMS category
```

## Modules

| Module | What it adds |
|---|---|
| `ke_base` | KRA PIN validation (`A`/`P` + 9 digits + letter) and normalisation, "KRA PIN" label, Kenyan MSISDN normalisation, phone masking. |
| `ke_etims` | Company eTIMS settings, eTIMS tax type (A–E) on taxes, item data on products, transmission queue + cron, "Send to eTIMS", credit-note rules, QR on invoice PDF, training connector. |
| `ke_mpesa` | M-Pesa accounts (till/paybill, credentials), STK push + query, C2B URL registration, callbacks with secret URLs and IP allow-list, transaction log, claims, crons. |
| `ke_pos` | POS M-Pesa terminal (dialog with STK push / code verification), eTIMS signing before printing, receipt additions, KES 1.00 rounding, eTIMS payment types, one-click M-Pesa payment method setup. |
| `ke_ecommerce_api` | Channels, hashed scoped API keys, REST endpoints, webhook outbox (HMAC signed, retried), request logs, stock change notifications, online payments → invoice → eTIMS. |
| `ke_cash_purchase` | Cash purchase wizard → PO, receipt, bill, payment; no input VAT without a supplier eTIMS invoice. |
| `ke_uac` | Cashier / Branch Manager roles; "Allowed shops" per user. |
| `ke_reporting` | Daily collections by payment method; VAT by eTIMS tax category. |

## Quick start (development)

Prerequisites: Docker + Compose, Git.

```bash
git clone <repo> odoo-kenya && cd odoo-kenya
docker compose up -d
docker compose logs -f odoo          # wait for "HTTP service ... running"
```

Create a Kenyan database (company country **must** be Kenya before the
accounting modules are installed):

```bash
docker compose exec odoo odoo -d odoo_kenya -i base --stop-after-init --no-http
docker compose exec -T -e SETUP_PHASE=company -e COMPANY_NAME="Duka Bora Ltd" \
    -e KRA_PIN=P051234567X -e BASE_URL=http://localhost:8069 \
    odoo odoo shell -d odoo_kenya --no-http < scripts/tenant_setup.py
docker compose exec odoo odoo -d odoo_kenya --stop-after-init --no-http \
    -i contacts,sale_management,sale_stock,purchase_stock,point_of_sale,ke_base,ke_etims,ke_mpesa,ke_pos,ke_ecommerce_api,ke_cash_purchase,ke_uac,ke_reporting
docker compose exec -T -e SETUP_PHASE=finalize -e BASE_URL=http://localhost:8069 \
    odoo odoo shell -d odoo_kenya --no-http < scripts/tenant_setup.py
docker compose restart odoo
```

The `company` phase prints the generated admin password. Open
<http://localhost:8069>, then follow [docs/POS_USER_GUIDE.md](docs/POS_USER_GUIDE.md) §2
(M-Pesa account, POS payment methods, users).

Iterating on code:

```bash
docker compose exec odoo odoo -d odoo_kenya -u ke_pos --stop-after-init --no-http
docker compose restart odoo
```

POS assets (JS/XML/SCSS) reload with `?debug=assets` on the POS URL.

M-Pesa callbacks need a public HTTPS URL: use a tunnel (ngrok,
cloudflared) and set it as *Callback base URL* on the M-Pesa account.

## Production

Target: the Contabo VPS, user `tom`, Docker Compose, nginx, Jenkins - see
[docs/MULTI_TENANCY.md](docs/MULTI_TENANCY.md) for details.

```bash
cd /home/tom/apps/odoo-kenya
cp .env.example .env && nano .env              # passwords, TENANT_DOMAIN, KE_MODULES
bash scripts/render-config.sh                  # writes config/odoo.conf (dbfilter ^%d$)
docker compose -f docker-compose.prod.yml up -d
sudo cp deploy/nginx/odoo-kenya.conf /etc/nginx/sites-available/odoo-kenya   # edit domain
sudo ln -s /etc/nginx/sites-available/odoo-kenya /etc/nginx/sites-enabled/
# wildcard certificate via DNS-01 (see MULTI_TENANCY.md), then:
sudo nginx -t && sudo systemctl reload nginx

bash scripts/tenant.sh create duka "Duka Bora Ltd" P051234567X owner@duka.co.ke
# → https://duka.pos.dima.co.ke
```

Deployments: the Jenkins pipeline (`deploy/Jenkinsfile`) pulls, backs up
**all** tenants, upgrades **all** tenants and health-checks. Manual
equivalent:

```bash
git pull origin main && bash scripts/render-config.sh
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d
bash scripts/tenant.sh backup-all && bash scripts/tenant.sh upgrade-all
docker compose -f docker-compose.prod.yml restart odoo
```

Backups: wire `scripts/backup.sh <dir>` into the existing db-backup-service
cron (custom-format dump + filestore per tenant, picked up by the GFS
rotation / rclone sync).

## Before going live

1. **eTIMS**: the built-in connector is *training mode* (numbers prefixed
   `TRAIN`, receipts marked "not a valid tax invoice", refused in production).
   Install a vetted OSCU/VSCU connector module (interface documented in
   [docs/ETIMS.md](docs/ETIMS.md)) and switch each company to it.
2. **M-Pesa**: production Daraja app, production M-Pesa account, C2B URLs
   registered, a KES 1 test sale ([docs/MPESA_SETUP.md](docs/MPESA_SETUP.md)).
3. **POS**: KES 1.00 rounding, receipt header/footer, cashiers' allowed shops,
   stock updated in real time if you sell online.
4. **API**: production API keys with minimal scopes, webhook secret shared,
   nginx rate limiting active.

## Tests

Every module has automated tests tagged `ke` (KRA PIN/phone rules, eTIMS
queue and credit notes, Daraja calls (mocked) and real HTTP callbacks, POS
orders with M-Pesa and eTIMS, double-claim protection, the whole REST API
over HTTP including webhook signatures and multi-company isolation, cash
purchases, shop restrictions, reports):

```bash
docker compose exec odoo odoo -d test_ke --stop-after-init --no-http -i base
# set the company to Kenya (tenant_setup.py company phase), then:
docker compose exec odoo odoo -d test_ke --stop-after-init --no-http \
    -i ke_base,ke_etims,ke_mpesa,ke_pos,ke_ecommerce_api,ke_cash_purchase,ke_uac,ke_reporting \
    --test-enable --test-tags ke
```

Last run on Odoo 19.0: **59 tests, 0 failures** on a fresh database.

## Upgrading from the original scaffold

The first version of this repository was a skeleton that could not run on
Odoo 19 as-is. If a database was created from it:

- `ke_pos_ui` is replaced by `ke_pos`: uninstall `ke_pos_ui`, install `ke_pos`.
- The M-Pesa log model is now `ke.mpesa.transaction` (was `mpesa.transaction`);
  M-Pesa credentials moved from POS payment methods to *M-Pesa accounts*.
- The M-Pesa callback URL changed from `/ke_mpesa/callback` (refused by
  Daraja) to `/ke/lipa/stk/<secret>`; see the M-Pesa account *Callbacks* tab.
- Invoices no longer fail to post without an eTIMS connector: they are queued.

The full list of Odoo 19 incompatibilities that were fixed is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#5-odoo-19-specifics-handled).

## Common operations

| Task | Command |
|---|---|
| Tail logs (dev) | `docker compose logs -f odoo` |
| Tail logs (prod) | `docker compose -f docker-compose.prod.yml exec odoo tail -f /var/log/odoo/odoo.log` |
| Odoo shell | `docker compose exec odoo odoo shell -d <db> --no-http` |
| Upgrade one module (dev) | `docker compose exec odoo odoo -d odoo_kenya -u ke_mpesa --stop-after-init --no-http` |
| List tenants (prod) | `bash scripts/tenant.sh list` |
| Upgrade one tenant | `bash scripts/tenant.sh upgrade <tenant>` |
| Back up one tenant | `bash scripts/tenant.sh backup <tenant>` |
| Pending eTIMS documents | Invoicing → Customers → Invoices, filter *eTIMS pending* (POS: Orders, same filter) |
| Unclaimed M-Pesa payments | Point of Sale → Orders → M-Pesa payments, filter *Unclaimed payments* |
| API errors | Sales → Configuration → E-commerce integration → API request logs |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Generic chart of accounts / `l10n_ke` missing | The company country was not Kenya before accounting was installed. Use `tenant_setup.py` (it sets the country first and loads the `ke` chart). |
| POS M-Pesa: "only accepts whole shillings" | Enable KES 1.00 cash rounding on the POS (not only for cash). |
| STK push never confirms | Callback URL not reachable; Odoo still confirms via status query after 15 s. Check *Callback base URL* (public HTTPS). |
| Receipt shows "Transmission to KRA pending" | KRA/connector unreachable; the queue retries every 5 minutes. |
| API 404 "No database is selected" | Call the tenant host (`https://<tenant>.<domain>`) or send `X-Odoo-Database`. |
| Webhooks not received | Channel webhook URL/events; *Webhook deliveries* shows each attempt and error. |
