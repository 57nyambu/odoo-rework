# Multi-tenancy operations

One server hosts many businesses ("tenants"). Each tenant gets:

- its own **PostgreSQL database** and filestore (complete isolation of data,
  users, settings, M-Pesa and eTIMS credentials, API keys, crons);
- its own **sub-domain**: `https://<tenant>.pos.dima.co.ke`;
- optionally several **companies** inside its database (branches registered
  separately with KRA, or several brands).

```
                 *.pos.dima.co.ke  (wildcard DNS + TLS)
                          │
                       nginx  ── /api/ke/ rate-limited per API key
                          │      /websocket → :8072
                          ▼
              Odoo 19 (workers, proxy_mode, dbfilter = ^%d$)
                │            │             │
            duka (db)   mama-mboga (db)  demo (db)
```

## 1. How a request finds its tenant

`dbfilter = ^%d$` in `config/odoo.prod.conf.template`: `%d` is the first
label of the host name, so `mama-mboga.pos.dima.co.ke` can only open the
database `mama-mboga`. `list_db = False` and nginx hide the database manager.

Consequences:

- Tenant name = database name = sub-domain: lowercase letters, digits, dashes.
- Safaricom callbacks and e-commerce API calls use the tenant host, so they
  land in the right database automatically (each tenant registers its own
  M-Pesa callback URLs, see the M-Pesa account *Callbacks* tab).
- An API client that must call a shared host can send
  `X-Odoo-Database: <tenant>`; the database must still match the filter for
  that host, so prefer tenant hosts.

## 2. DNS and TLS

1. DNS: `pos.dima.co.ke` and `*.pos.dima.co.ke` → server IP (A records).
2. Wildcard certificate (needs a DNS-01 challenge), e.g. with Cloudflare DNS:

   ```bash
   sudo apt-get install -y python3-certbot-dns-cloudflare
   sudo certbot certonly --dns-cloudflare \
        --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
        -d pos.dima.co.ke -d '*.pos.dima.co.ke'
   ```

3. `deploy/nginx/odoo-kenya.conf` → `/etc/nginx/sites-available/`, adjust the
   domain, `nginx -t && systemctl reload nginx`.

## 3. Configuration

`.env` (from `.env.example`):

| Variable | Purpose |
|---|---|
| `TENANT_DOMAIN` | Parent domain of tenant sub-domains. |
| `ODOO_DBFILTER` | `^%d$` (one tenant per sub-domain). |
| `ODOO_WORKERS` | Odoo workers (≈ 2 × CPU + 1). Multi-worker mode is required for the POS websocket on port 8072. |
| `KE_MODULES` | Modules installed in every new tenant. |
| `BACKUP_DIR` | Default destination of `backup-all`. |

Render the Odoo config with `bash scripts/render-config.sh` whenever `.env`
changes.

## 4. Tenant lifecycle (`scripts/tenant.sh`)

```bash
# Create: database, Kenyan company, modules, chart of accounts, defaults
bash scripts/tenant.sh create mama-mboga "Mama Mboga Enterprises" P051234567X owner@mamamboga.co.ke

bash scripts/tenant.sh list
bash scripts/tenant.sh upgrade mama-mboga            # after a code update (one tenant)
bash scripts/tenant.sh upgrade-all                   # all tenants (used by Jenkins)
bash scripts/tenant.sh backup mama-mboga             # pg_dump -Fc + filestore tarball
bash scripts/tenant.sh backup-all
bash scripts/tenant.sh drop mama-mboga               # asks to type the name; back up first
```

`create` performs, in order:

1. `odoo -i base` on a new database named after the tenant.
2. Company: name, **Kenya**, **KES**, KRA PIN, admin password (printed once),
   admin login/e-mail, `web.base.url = https://<tenant>.<domain>`
   (frozen, so links and callbacks always use the tenant host).
3. Installs `KE_MODULES`.
4. Loads the Kenyan chart of accounts (`l10n_ke`), sets **prices VAT
   inclusive**, enables eTIMS in **training mode**, KES 1.00 rounding on the
   shops.

Then hand over to the business: [POS_USER_GUIDE.md](POS_USER_GUIDE.md) §2.

### Restore a tenant

```bash
docker compose -f docker-compose.prod.yml exec -T db createdb -U "$ODOO_DB_USER" mama-mboga
docker compose -f docker-compose.prod.yml exec -T db pg_restore -U "$ODOO_DB_USER" -d mama-mboga < mama-mboga-20261015-0200.dump
docker compose -f docker-compose.prod.yml exec -T odoo sh -c 'mkdir -p /var/lib/odoo/filestore/mama-mboga && tar -xzf - -C /var/lib/odoo/filestore/mama-mboga' < mama-mboga-filestore-20261015-0200.tar.gz
```

A restored copy under another name (e.g. `mama-mboga-staging`) must not
talk to Safaricom/KRA production: archive its M-Pesa accounts and switch
eTIMS to sandbox/training before opening it.

## 5. Deployments

`deploy/Jenkinsfile`:

1. Static checks (Python compiles, XML parses).
2. On the server: `git pull`, render config, pull images, `up -d`,
   **backup all tenants**, **upgrade all tenants**, restart Odoo.
3. Health check `https://<a tenant>/web/login` through nginx.

`upgrade-all` stops with the list of tenants that failed; the others are
upgraded. Fix and rerun for the failed ones (`upgrade <tenant>`).

## 6. Isolation guarantees and limits

| Concern | Between tenants | Between companies of one tenant |
|---|---|---|
| Business data | Separate databases | Record rules (`company_id in company_ids`) |
| Users | Separate | Users can be given access to several companies |
| M-Pesa credentials & callbacks | Separate URLs/secrets per tenant | Per M-Pesa account, company-bound |
| eTIMS | Separate | Per company (KRA PIN + branch id) |
| E-commerce API | Keys valid in one database only | Key → channel → company; technical user limited to that company |
| Crons | Run per database | Process all companies of the tenant |
| Resources (CPU/RAM) | **Shared** Odoo workers and PostgreSQL | Shared |

For a tenant needing dedicated resources, run a second stack (another
compose project with its own port and `dbfilter`) and route its sub-domain to
it in nginx.

## 7. Multi-company inside a tenant

Create branch companies under *Settings → Companies* (parent = head office)
when branches are registered separately with KRA (own `bhfId`), or for
separate brands. Per company: eTIMS settings, M-Pesa accounts, POS shops,
warehouses, e-commerce channels. Users switch companies from the top bar;
reports and record rules follow the selected companies.

## 8. Local testing of sub-domain tenancy

Browsers resolve `*.localhost` to 127.0.0.1. Uncomment `dbfilter = ^%d$` in
`config/odoo.conf`, create databases `shop1` and `shop2`, then open
`http://shop1.localhost:8069` and `http://shop2.localhost:8069`.
