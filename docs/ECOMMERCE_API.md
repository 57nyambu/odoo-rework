# E-commerce Exchange API - reference (v1)

REST API exposed by the `ke_ecommerce_api` module so an online shop
(WooCommerce, Shopify app, Magento, a custom Laravel / Django / Node store,
a marketplace...) can exchange data with the business's Odoo:

- read the **catalog** (prices VAT inclusive, eTIMS tax type) and **stock** per branch;
- push **orders** (home delivery or pickup at a branch) and **customers** (with KRA PIN);
- record **payments** (M-Pesa codes, card / bank references) or ask Odoo to send an **M-Pesa STK push**;
- follow **fulfilment**, **invoices** and their **KRA eTIMS** signature;
- receive **webhooks** for every change.

Machine-readable spec: [`openapi.yaml`](../addons-custom/ke_ecommerce_api/static/api/openapi.yaml),
also served by every tenant at `https://<tenant host>/ke_ecommerce_api/static/api/openapi.yaml`
(import it in Postman, Insomnia or a code generator).
Step-by-step integration: [ECOMMERCE_INTEGRATION_GUIDE.md](ECOMMERCE_INTEGRATION_GUIDE.md).

---

## 1. Basics

### Base URL and tenant

```
https://<tenant>.pos.example.co.ke/api/ke/v1
```

Each business (tenant) has its own host and database; the host selects the
tenant. If you must call a shared host, add `X-Odoo-Database: <tenant>`.

### Authentication

```
Authorization: Bearer ke_3f9a1c2b7d_Qm9...remaining-secret...
```

- API keys are created in Odoo by the business: *Sales → Configuration →
  E-commerce integration → Channels → (channel) → New API key*. The key is
  shown **once**; Odoo stores only a hash.
- A key belongs to one **channel** (one online shop) and one company. All
  calls run as the channel's technical user, restricted to that company.
- Keys have **scopes**, an optional expiry, and can be revoked at any time
  (toggle *active* on the key).
- Server-to-server only: never embed a key in a browser, mobile app or
  front-end bundle.

| Scope | Grants |
|---|---|
| `catalog:read` | `/products`, `/categories`, `/pickup-locations` |
| `stock:read` | `/stock` |
| `customers:read` / `customers:write` | read / upsert customers |
| `orders:read` | read orders, STK request status |
| `orders:write` | create and cancel orders |
| `payments:write` | record payments, request STK pushes |
| `webhooks:read` / `webhooks:write` | list / test / redeliver webhook events |

`/ping` and `/counties` only need a valid key.

### Conventions

| Topic | Rule |
|---|---|
| Format | JSON (`Content-Type: application/json`), UTF-8. |
| Money | Decimal numbers in the channel currency (`KES`). Prices are **VAT inclusive** unless you send `"prices_include_tax": false`. |
| Dates | ISO 8601. Responses in UTC (`2026-10-15T07:15:00Z`). Inputs without offset are read as East Africa Time (UTC+3). |
| Pagination | `page` (from 1) and `page_size` (≤ 200, default 50); responses carry `meta: {page, page_size, total, has_more}`. |
| Idempotency | Orders are unique per `external_id`, payments per `reference` (per channel). Resending returns the existing record with **200** and `"replayed": true` instead of 201. Retry freely after timeouts. |
| Request id | Every response has `X-Request-Id` (send your own to correlate). Errors repeat it in the body. Quote it to support. |
| Rate limit | Per key and minute (channel setting, default 120) plus nginx burst control. Headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`; `429` comes with `Retry-After`. |
| Atomicity | A request that fails changes nothing. |

### Errors

```json
{
  "error": {
    "code": "insufficient_stock",
    "message": "Some items are not available in the requested quantity.",
    "details": {"items": [{"product_id": 41, "sku": "SODA-500", "requested": 50, "available": 10.0}]},
    "request_id": "6d0f3b8e3c5f4c1f9a2e1b0c7d8e9f10"
  }
}
```

| HTTP | `code` | Meaning / what to do |
|---|---|---|
| 400 | `invalid_json`, `invalid_parameter` | Fix the request. |
| 401 | `unauthorized` | Missing, wrong, revoked or expired key. |
| 403 | `insufficient_scope`, `forbidden` | Key lacks the scope / access. |
| 404 | `not_found` | Unknown order, product (or not published), customer, event. |
| 409 | `insufficient_stock` | Not enough stock; `details.items` lists shortages. Show "only N left". |
| 409 | `already_fulfilled`, `order_cancelled`, `mpesa_payment_already_used`, `nothing_to_pay` | State conflict. |
| 422 | `validation_error` | Missing/invalid field; `details` names it. |
| 422 | `unknown_product`, `invalid_county`, `invalid_kra_pin`, `invalid_phone`, `currency_mismatch`, `invalid_pickup_location`, `invalid_mpesa_code`, `mpesa_payment_not_found`, `mpesa_amount_mismatch`, `mpesa_not_configured`, `mpesa_request_failed`, `no_payment_journal` | Business rule; see message. |
| 429 | `rate_limited` | Wait `Retry-After` seconds. |
| 500 | `server_error` | Retry with backoff (idempotency makes it safe); report the `request_id` if it persists. |

---

## 2. General

### `GET /ping`

Check the key and see which tenant/company you are talking to.

```bash
curl -s https://duka.pos.example.co.ke/api/ke/v1/ping -H "Authorization: Bearer $KE_API_KEY"
```

```json
{
  "status": "ok",
  "api_version": "1.0",
  "database": "duka",
  "server_time": "2026-10-15T07:15:02Z",
  "channel": {"code": "duka-online", "name": "Duka Online"},
  "company": {"name": "Duka Bora Ltd", "kra_pin": "P051234567X", "currency": "KES"}
}
```

### `GET /counties`

The 47 counties with their ISO codes, for address forms. Addresses accept
the code (`KE-30`), the number (`30`) or the name (`Nairobi`).

```json
{"data": [{"code": "KE-01", "name": "Baringo"}, "...", {"code": "KE-30", "name": "Nairobi City"}, "..."]}
```

### `GET /pickup-locations` - `catalog:read`

Branches where customers can collect orders.

```json
{"data": [{"id": 3, "code": "WSTL", "name": "Westlands branch", "street": "Waiyaki Way",
           "city": "Nairobi", "county": "Nairobi City", "county_code": "KE-30", "phone": "+254700000001"}]}
```

### `GET /categories` - `catalog:read`

```json
{"data": [{"id": 1, "name": "All", "full_name": "All", "parent_id": null},
          {"id": 7, "name": "Beverages", "full_name": "All / Beverages", "parent_id": 1}]}
```

---

## 3. Catalog

### `GET /products` - `catalog:read`

Products published on the channel (ticked *Online channels* on the product,
or all saleable products if the channel publishes everything).

| Query | Description |
|---|---|
| `page`, `page_size` | Pagination. |
| `updated_since` | Only products changed after this time (incremental sync). |
| `sku`, `barcode` | Comma-separated exact matches. |
| `category_id` | Category and sub-categories. |
| `q` | Search name / SKU. |
| `include_archived` | Also return archived products (`active: false`) so you can unpublish them. |

```json
{
  "data": [
    {
      "id": 41,
      "template_id": 37,
      "sku": "SODA-500",
      "barcode": "6161234500017",
      "name": "Soda 500ml",
      "description": null,
      "category": {"id": 7, "name": "All / Beverages"},
      "type": "goods",
      "uom": "Units",
      "variant_attributes": [],
      "active": true,
      "published": true,
      "currency": "KES",
      "price": 116.0,
      "price_excluding_tax": 100.0,
      "tax_amount": 16.0,
      "taxes": [{"name": "16%", "rate": 16.0, "included_in_price": true}],
      "etims_tax_type": "B",
      "track_stock": true,
      "available_quantity": 10.0,
      "weight": null,
      "image_url": "/api/ke/v1/products/41/image",
      "updated_at": "2026-10-15T06:40:11Z"
    }
  ],
  "meta": {"page": 1, "page_size": 50, "total": 1, "has_more": false}
}
```

- `price` is what the customer pays per unit (pricelist of the channel, VAT included).
- `etims_tax_type`: `A` exempt, `B` 16%, `C` zero rated, `D` non-VAT, `E` 8%.
- `available_quantity` is `null` for services and goods not tracked in stock.

### `GET /products/{id}` - `catalog:read`

Same object. `404` if the product is not published on the channel.

### `GET /products/{id}/image?size=1024` - `catalog:read`

The image as binary (`size` 128, 256, 512 or 1024). Download once and cache;
re-download when `updated_at` changes. The URL needs the API key, so do not
hot-link it in your storefront.

---

## 4. Stock

### `GET /stock` - `stock:read`

| Query | Description |
|---|---|
| `skus` / `product_ids` | Comma-separated filter. |
| `location_id` | Pickup branch id or code; default: the channel's shipping warehouse. |
| `page`, `page_size` | Pagination. |

```json
{
  "data": [
    {"product_id": 41, "sku": "SODA-500", "track_stock": true, "available_quantity": 8.0, "in_stock": true}
  ],
  "meta": {"page": 1, "page_size": 50, "total": 1, "has_more": false,
           "location": {"id": 1, "code": "WH", "name": "Duka Bora Ltd", "...": "..."}}
}
```

`available_quantity` = on hand − reserved (including unshipped online and
shop orders) − the channel's *safety buffer*. Shop (POS) sales reduce it in
real time when the POS updates stock in real time.

---

## 5. Customers

### `POST /customers` - `customers:write`

Creates or updates (upsert) a customer. Matching order: your `external_id`
(per channel), then e-mail, then phone. Optional: you can send the customer
inline when creating an order instead.

```json
{
  "external_id": "cust-77",
  "name": "Achieng Otieno",
  "email": "achieng@example.co.ke",
  "phone": "0712 345 678",
  "kra_pin": "A012345678Z",
  "company_name": null
}
```

- `phone`: any Kenyan mobile format; stored as `+2547XXXXXXXX`.
- `kra_pin`: `A` (individuals) or `P` (companies) + 9 digits + letter.
  Required by business buyers to claim input VAT: it is printed on the
  invoice and sent to eTIMS.
- `company_name`: bill a business (the customer record becomes the company).

Response `200`:

```json
{"data": {"id": 88, "external_id": "cust-77", "name": "Achieng Otieno", "email": "achieng@example.co.ke",
          "phone": "+254712345678", "kra_pin": "A012345678Z", "is_company": false}}
```

### `GET /customers/{id}` - `customers:read`

---

## 6. Orders

### `POST /orders` - `orders:write`

```json
{
  "external_id": "WC-10023",
  "currency": "KES",
  "placed_at": "2026-10-15T10:15:00+03:00",
  "prices_include_tax": true,
  "customer": {
    "external_id": "cust-77",
    "name": "Achieng Otieno",
    "email": "achieng@example.co.ke",
    "phone": "0712345678",
    "kra_pin": "A012345678Z"
  },
  "shipping_address": {
    "name": "Achieng Otieno",
    "phone": "0712345678",
    "street": "Ngong Road, Prestige Plaza",
    "street2": "3rd floor",
    "city": "Nairobi",
    "county": "Nairobi",
    "postal_code": "00100"
  },
  "fulfilment": {
    "type": "delivery",
    "shipping_fee": 200,
    "carrier": "Sendy",
    "instructions": "Call on arrival"
  },
  "lines": [
    {"sku": "SODA-500", "quantity": 2, "unit_price": 116},
    {"sku": "UNGA-2KG", "quantity": 1, "unit_price": 180, "discount_percent": 0}
  ],
  "note": "Gift wrap",
  "payment": null
}
```

| Field | Rules |
|---|---|
| `external_id` | Required, ≤ 64 chars, unique per channel: your order number. |
| `lines[]` | ≥ 1 line; product by `product_id`, `sku` or `barcode`; must be published; `quantity` > 0. |
| `lines[].unit_price` | The price shown to the customer. With price source *Odoo pricelist* (default) Odoo uses its own price and adds a `price_overridden` warning when they differ; with *platform prices* Odoo books your price. |
| `fulfilment.type` | `delivery` (needs `shipping_address`) or `pickup` (needs `pickup_location_id`: branch id or code). |
| `fulfilment.shipping_fee` | Added as a "Delivery" line (16% VAT). |
| `shipping_address.county` | County name, code (`KE-30`) or number. |
| `payment` | Optional: same body as `POST /orders/{id}/payments` when the shop already collected the money. |

Behaviour:

1. Stock is checked in the shipping (or pickup) warehouse unless the channel
   allows backorders → `409 insufficient_stock`.
2. The customer is matched or created (KRA PIN validated).
3. The sale order is created and, with *auto confirm* (default), confirmed:
   stock is **reserved** and a delivery order created in the warehouse.
4. Webhooks `order.created` and `order.confirmed` are queued.

Response `201` (`200` + `"replayed": true` when the `external_id` exists):

```json
{
  "data": {
    "id": 42,
    "name": "S00042",
    "external_id": "WC-10023",
    "status": "ready",
    "payment_status": "unpaid",
    "currency": "KES",
    "amount_untaxed": 552.41,
    "amount_tax": 59.59,
    "amount_total": 612.0,
    "amount_paid": 0.0,
    "amount_due": 612.0,
    "tax_breakdown": [
      {"etims_tax_type": "A", "base": 180.0, "tax": 0.0},
      {"etims_tax_type": "B", "base": 372.41, "tax": 59.59}
    ],
    "customer": {"id": 88, "external_id": "cust-77", "name": "Achieng Otieno",
                 "email": "achieng@example.co.ke", "phone": "+254712345678",
                 "kra_pin": "A012345678Z", "is_company": false},
    "shipping_address": {"name": "Achieng Otieno", "phone": "+254712345678",
                         "street": "Ngong Road, Prestige Plaza", "street2": "3rd floor",
                         "city": "Nairobi", "county": "Nairobi City", "county_code": "KE-30",
                         "postal_code": "00100", "country": "KE"},
    "fulfilment": {
      "type": "delivery", "carrier": "Sendy", "instructions": "Call on arrival",
      "pickup_location": null,
      "shipments": [{"id": 17, "name": "WH/OUT/00017", "state": "assigned",
                     "scheduled_date": "2026-10-15T07:15:03Z", "date_done": null,
                     "tracking_reference": null}]
    },
    "lines": [
      {"id": 90, "product_id": 41, "sku": "SODA-500", "name": "Soda 500ml", "quantity": 2.0,
       "quantity_delivered": 0.0, "quantity_invoiced": 0.0, "unit_price": 116.0,
       "discount_percent": 0.0, "subtotal": 200.0, "tax": 32.0, "total": 232.0,
       "etims_tax_type": "B", "is_delivery_fee": false},
      {"id": 91, "product_id": 42, "sku": "UNGA-2KG", "name": "Unga 2kg", "quantity": 1.0,
       "quantity_delivered": 0.0, "quantity_invoiced": 0.0, "unit_price": 180.0,
       "discount_percent": 0.0, "subtotal": 180.0, "tax": 0.0, "total": 180.0,
       "etims_tax_type": "A", "is_delivery_fee": false},
      {"id": 92, "product_id": 43, "sku": "DELIVERY-DUKA-ONLINE", "name": "Delivery (Sendy)",
       "quantity": 1.0, "quantity_delivered": 0.0, "quantity_invoiced": 0.0, "unit_price": 200.0,
       "discount_percent": 0.0, "subtotal": 172.41, "tax": 27.59, "total": 200.0,
       "etims_tax_type": "B", "is_delivery_fee": true}
    ],
    "invoices": [],
    "payments": [],
    "note": "Gift wrap",
    "placed_at": "2026-10-15T07:15:00Z",
    "created_at": "2026-10-15T07:15:03Z",
    "updated_at": "2026-10-15T07:15:03Z"
  },
  "warnings": []
}
```

#### Order status

| `status` | Meaning |
|---|---|
| `pending` | Received, not confirmed (channel without auto confirm). |
| `confirmed` | Confirmed; stock not (fully) reserved yet. |
| `ready` | All goods reserved: ready to pick/pack (for pickup orders: can be prepared for collection). |
| `partially_fulfilled` | Part of the goods delivered / collected. |
| `fulfilled` | Delivered / collected (delivery order validated in Odoo). |
| `cancelled` | Cancelled. |

`payment_status`: `unpaid`, `partial`, `paid`.

### `GET /orders/{external_id}` - `orders:read`

The order object above, with current shipments, invoices (and their eTIMS
status) and payments.

### `GET /orders` - `orders:read`

Query: `updated_since`, `status` (`pending`, `confirmed`, `cancelled`),
`payment_status`, `page`, `page_size`. Newest first.

### `POST /orders/{external_id}/cancel` - `orders:write`

```json
{"reason": "Customer changed mind"}
```

- Releases reserved stock and cancels the delivery.
- `409 already_fulfilled` if goods left the warehouse (do a return in Odoo).
- If an invoice was posted it is reversed by a **credit note** (transmitted
  to eTIMS). Money already received stays recorded: refund the customer on
  your payment provider and let the business record the refund in Odoo.

---

## 7. Payments

### `POST /orders/{external_id}/payments` - `payments:write`

Record money the shop collected (M-Pesa paybill/till via your own Daraja
integration, card through Pesapal/Flutterwave/DPO/Stripe, bank transfer,
cash on delivery...).

```json
{
  "method": "mpesa",
  "reference": "SJK3WEB001",
  "amount": 612,
  "paid_at": "2026-10-15T10:17:45+03:00",
  "phone": "0712345678"
}
```

| Field | Rules |
|---|---|
| `method` | `mpesa`, `airtel_money`, `card`, `bank_transfer`, `cash_on_delivery`, `other`. |
| `reference` | Unique per channel. For `mpesa` the 10-character M-Pesa code (case-insensitive). |
| `amount` | > 0. Several partial payments are allowed. |

When the channel verifies M-Pesa codes (*Check against Safaricom
notifications*), the code must match a payment Safaricom reported to the
business's till/paybill (C2B), with an amount ≥ `amount`, not already used:
otherwise `422 mpesa_payment_not_found` / `409 mpesa_payment_already_used` /
`422 mpesa_amount_mismatch`.

What Odoo does:

1. Books the payment in the channel's journal (M-Pesa journal for `mpesa`).
2. When the order is **fully paid** and the channel has *auto invoice*
   (default): creates and posts the invoice, reconciles the payments and
   queues the invoice for **KRA eTIMS**. (Products invoiced on delivery are
   invoiced when the delivery is validated.)
3. Webhooks `payment.received`, `order.paid`, `order.invoiced`, later
   `invoice.etims_signed`.

Response `201` (`200` + `"replayed": true` for a known reference):

```json
{
  "data": {
    "payment": {"id": 55, "method": "mpesa", "reference": "SJK3WEB001", "amount": 612.0,
                "currency": "KES", "date": "2026-10-15", "state": "paid"},
    "order": {"...": "order object, payment_status: paid, invoices: [ ... ]"}
  }
}
```

Invoice object inside orders:

```json
{
  "id": 77,
  "number": "INV/2026/00031",
  "type": "invoice",
  "state": "posted",
  "date": "2026-10-15",
  "amount_untaxed": 552.41,
  "amount_tax": 59.59,
  "amount_total": 612.0,
  "amount_residual": 0.0,
  "payment_state": "paid",
  "etims": {
    "status": "sent",
    "cu_invoice_number": "KRACU0100000001/152",
    "scu_id": "KRACU0100000001",
    "receipt_number": 152,
    "signed_at": "2026-10-15T07:18:02Z",
    "qr_data": "https://etims.kra.go.ke/common/link/etims/receipt/indexEtimsReceiptData?Data=...",
    "training": false
  },
  "portal_url": "https://duka.pos.example.co.ke/my/invoices/77?access_token=..."
}
```

Show customers the `cu_invoice_number`, a QR code of `qr_data` and the
`portal_url` link (PDF invoice with the eTIMS block). `etims.status`
`to_send` means "being transmitted"; `training: true` means the business is
still in eTIMS training mode (not a valid tax invoice).

### `POST /orders/{external_id}/mpesa-stk-push` - `payments:write`

Let Odoo collect the M-Pesa payment with the business's own till/paybill
(channel *M-Pesa account*).

```json
{"phone": "0712345678", "amount": 612}
```

Both fields are optional (default: customer phone, amount due). `202`:

```json
{"data": {"id": 9, "status": "pending", "checkout_request_id": "ws_CO_15102026101800123456",
          "amount": 612.0, "phone": "0712***678", "receipt_number": null,
          "message": "Success. Request accepted for processing"}}
```

The customer confirms on their phone; then Odoo records the payment itself
(same effects as `POST /payments`) and sends `payment.received`. Failures
(cancelled, wrong PIN, insufficient funds, timeout) send `payment.failed`.

### `GET /mpesa/stk-requests/{id}` - `orders:read`

Poll the STK request (every 3–5 s, max ~2 min) if you don't use webhooks.
`status`: `pending`, `confirmed`, `failed`, `cancelled`, `expired`;
`message` explains failures in plain English.

---

## 8. Webhooks

Configure on the channel: *Webhook URL* (HTTPS), *Events sent*, and share
the *webhook secret* with your server.

### Events

| `type` | When | `data` |
|---|---|---|
| `order.created` | Order received through the API | Order |
| `order.confirmed` | Order confirmed (stock reserved) | Order |
| `order.paid` | Order fully paid | Order |
| `order.invoiced` | Invoice / credit note posted | Order |
| `invoice.etims_signed` | Invoice signed by KRA eTIMS | `{order: {id, name, external_id}, invoice}` |
| `order.fulfilled` | Delivery validated / pickup handed over (also partial) | Order |
| `order.cancelled` | Order cancelled (API or Odoo) | Order |
| `payment.received` | Payment recorded (API or STK push) | `{order, payment}` |
| `payment.failed` | STK push failed / cancelled | `{order, method, checkout_request_id, status, message}` |
| `stock.updated` | Available stock changed (checked every 5 min, batches of ≤ 200) | `{items: [{product_id, sku, available_quantity}]}` |
| `product.updated` | Name, price, SKU, barcode, taxes, category, image, archiving changed | Product. When the product is removed from the channel: `{id, sku, published: false, active: false}` - hide it. |
| `ping` | Test from Odoo or `POST /webhooks/test` | `{message}` |

### Delivery

```http
POST /odoo-webhooks HTTP/1.1
Content-Type: application/json
User-Agent: Odoo-Kenya-Webhooks/1.0
X-KE-Event: order.paid
X-KE-Event-Id: evt_6b0f6a0e5b6f4b0f9d8e2a1c3b4d5e6f
X-KE-Delivery-Attempt: 1
X-KE-Signature: t=1792041593,v1=5d1c0f4e...e9a3

{"id":"evt_6b0f6a0e5b6f4b0f9d8e2a1c3b4d5e6f","type":"order.paid","created_at":"2026-10-15T07:17:46Z",
 "api_version":"1.0","channel":"duka-online","data":{...order...}}
```

- **Verify** every request: `v1` = hex HMAC-SHA256 of `"<t>.<raw body>"`
  with the webhook secret; reject if it doesn't match or `t` is older than
  5 minutes (replay protection). Use the raw body bytes, not re-serialised
  JSON. Code samples in the integration guide.
- **Answer 2xx within 15 s**; do slow work asynchronously.
- **Retries** on non-2xx/timeouts after 1, 5, 15, 60, 180, 360, 720, 1440
  minutes, then the event is marked failed (visible in Odoo and via
  `GET /webhooks/events`, redeliverable).
- **At-least-once**: deduplicate on `id`. Order is not guaranteed across
  events; use `data.updated_at` or re-fetch `GET /orders/{id}` when in doubt.
- Events are written in the same transaction as the change and sent after
  commit: you never receive an event for a change that did not happen.

### `GET /webhooks/events` - `webhooks:read`

Recent deliveries (`state=pending|sent|failed|cancelled`).

### `POST /webhooks/test` - `webhooks:write`

Queues a `ping` event (`202`).

### `POST /webhooks/events/{event_id}/redeliver` - `webhooks:write`

---

## 9. Kenya specifics

| Topic | API behaviour |
|---|---|
| VAT | Prices VAT inclusive; 16% (B), 8% (E), zero rated (C), exempt (A), non-VAT (D). `tax_breakdown` per order. |
| KRA PIN | Validated format; printed on invoices and sent to eTIMS for B2B. |
| eTIMS | Every invoice is transmitted; `etims` block on invoices; `invoice.etims_signed` webhook. |
| M-Pesa | Codes validated (10 chars), optional verification against Safaricom notifications, STK push through Odoo. M-Pesa only accepts whole shillings. |
| Counties | 47 counties (`GET /counties`); `county_code` in addresses. |
| Phone numbers | Normalised to `+2547XXXXXXXX`; masked in STK responses. |
| Click & collect | `fulfilment.type = pickup` + branch from `GET /pickup-locations`. |
| Data Protection Act 2019 | Request bodies are only logged for failed calls and purged after 30 days; send only the personal data needed for the order. |

## 10. Versioning

The version is in the path (`/v1`) and in `X-KE-API-Version`. Additive
changes (new fields, new events, new optional parameters) can happen within
v1: ignore unknown fields. Breaking changes get a new version path.
