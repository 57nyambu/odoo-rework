# E-commerce integration guide

Step-by-step guide for developers connecting an online shop to a business's
Odoo (POS + inventory + accounting + KRA eTIMS). The endpoint reference is
[ECOMMERCE_API.md](ECOMMERCE_API.md); this guide explains *how to use it*
end to end, with code.

```
 Online shop (your server)                        Odoo tenant (https://duka.pos.example.co.ke)
 ─────────────────────────                        ───────────────────────────────────────────
 catalog / stock cache   ◀── GET /products, /stock ──  products, prices (VAT incl.), branch stock
                          ◀── webhooks product.updated / stock.updated
 checkout ── POST /orders ───────────────────────────▶ sale order, stock reserved, customer + KRA PIN
 payment  ── POST /orders/{id}/payments ─────────────▶ payment booked → invoice → KRA eTIMS
      or  ── POST /orders/{id}/mpesa-stk-push ───────▶ Odoo sends STK push via its own till/paybill
 order page / e-mails ◀── webhooks order.*, payment.*, invoice.etims_signed
```

## 0. Decide the integration pattern

| Question | Option A | Option B |
|---|---|---|
| Who collects M-Pesa? | **The shop** (its own Daraja app, or a gateway such as Pesapal/Flutterwave/DPO/iPay). It then reports the payment to Odoo. | **Odoo** sends the STK push with the business's till/paybill (`/mpesa-stk-push`). No Daraja work in the shop. |
| Cards | Via the shop's gateway, reported to Odoo. | Same. |
| Prices | Odoo is the source (recommended): the shop displays Odoo prices. | The shop sets prices; channel *price source = platform*. |
| Stock | Odoo is the source; the shop caches it and listens to `stock.updated`. | Same. |

Most Kenyan shops use **Option B for M-Pesa** (simplest, the money lands
directly in the business's till and is reconciled in Odoo) and Option A for
cards.

## 1. Get access (business side, in Odoo)

1. *Sales → Configuration → E-commerce integration → Channels → New*:
   - Name, code (e.g. `duka-online`), ships-from warehouse, pickup branches,
     pricelist, price source, safety buffer.
   - *Orders & payments*: auto confirm, auto invoice, online payments journal,
     M-Pesa journal, **M-Pesa account** (for Option B), M-Pesa verification.
   - *Webhooks*: your HTTPS endpoint, events, copy the **webhook secret**.
2. **New API key** → choose scopes → copy the key (shown once).
3. Publish products: tick the channel in *Online channels* on each product
   (or *Publish all products* on the channel).
4. POS settings: *Inventory → Update quantities in real time* so shop sales
   immediately reduce online stock.

Give the developer: tenant URL, API key, webhook secret.

Store them as server secrets:

```bash
KE_API_BASE=https://duka.pos.example.co.ke/api/ke/v1
KE_API_KEY=ke_3f9a1c2b7d_...
KE_WEBHOOK_SECRET=9c1e...
```

## 2. Check connectivity

```bash
curl -s "$KE_API_BASE/ping" -H "Authorization: Bearer $KE_API_KEY" | jq
```

You should see the business name, its KRA PIN and `KES`.

A minimal client (Node.js 18+, no dependency):

```js
// ke-client.mjs
const BASE = process.env.KE_API_BASE;
const KEY = process.env.KE_API_KEY;

export async function ke(method, path, body) {
  for (let attempt = 1; ; attempt++) {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 429 || res.status >= 500) {
      if (attempt >= 5) throw new Error(`KE API ${res.status} after ${attempt} attempts`);
      const wait = Number(res.headers.get("Retry-After")) || 2 ** attempt;
      await new Promise((r) => setTimeout(r, wait * 1000));
      continue; // safe: orders/payments are idempotent
    }
    const payload = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
    if (!res.ok) {
      const err = new Error(payload?.error?.message || res.statusText);
      Object.assign(err, { status: res.status, code: payload?.error?.code, details: payload?.error?.details,
                           requestId: payload?.error?.request_id });
      throw err;
    }
    return payload;
  }
}
```

PHP equivalent:

```php
function ke(string $method, string $path, ?array $body = null): array {
    $ch = curl_init(getenv('KE_API_BASE') . $path);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . getenv('KE_API_KEY'),
            'Content-Type: application/json',
        ],
        CURLOPT_POSTFIELDS => $body ? json_encode($body) : null,
    ]);
    $raw = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $data = json_decode($raw ?: 'null', true);
    if ($status >= 400) {
        throw new RuntimeException(($data['error']['code'] ?? 'http_' . $status) . ': ' .
            ($data['error']['message'] ?? 'request failed') . ' [' . ($data['error']['request_id'] ?? '-') . ']');
    }
    return $data ?? [];
}
```

## 3. Catalog sync

**Initial import** - page through everything:

```js
let page = 1, products = [];
for (;;) {
  const { data, meta } = await ke("GET", `/products?page=${page}&page_size=200&include_archived=1`);
  products.push(...data);
  if (!meta.has_more) break;
  page++;
}
```

Map to your catalog:

| Odoo field | Shop field |
|---|---|
| `id` | external id of the product/variant (keep it: you need it, or `sku`, in orders) |
| `sku`, `barcode` | SKU, GTIN |
| `name`, `description`, `category.name` | title, description, category |
| `price` | regular price (VAT inclusive) |
| `etims_tax_type`, `taxes` | tax class (for display; Odoo computes the VAT) |
| `available_quantity`, `track_stock` | stock quantity, manage stock |
| `active` | publish / unpublish |
| `image_url` | download with the API key (`GET /products/{id}/image`), upload to your media store |

**Keep it fresh**:

- subscribe to `product.updated` (sent on name/price/SKU/tax/image changes and
  when a product is published/unpublished on the channel), and
- run an incremental sync every hour as a safety net:
  `GET /products?updated_since=<last sync>&include_archived=1`.

## 4. Stock

- Show `available_quantity` from your cache.
- Subscribe to `stock.updated`: `data.items[]` lists products whose
  availability changed (checked every 5 minutes; includes shop sales).
- At **checkout**, re-check the cart: `GET /stock?skus=SODA-500,UNGA-2KG`
  (or for click & collect `&location_id=WSTL`). Creating the order is the
  real reservation: if stock disappeared meanwhile you get
  `409 insufficient_stock` with the available quantities - show "Only N
  left" and let the customer adjust.

## 5. Checkout → create the order

Create the order **when the customer places it** (before or after payment,
see §6). Use your order number as `external_id`; retrying the same request
never creates a duplicate.

### Fields to capture at checkout (Kenya)

| Field | Why |
|---|---|
| Phone (`07XX XXX XXX`) | Delivery rider, M-Pesa STK push. |
| County (dropdown from `GET /counties`) + town/area + street/building | Delivery address. |
| Delivery or pickup branch (`GET /pickup-locations`) | Click & collect is popular and cheaper. |
| *"I need a VAT invoice"* → **KRA PIN** (+ company name) | Business buyers need their PIN on the eTIMS invoice to claim input VAT. |

### Example (WooCommerce, PHP)

```php
add_action('woocommerce_checkout_order_processed', function (int $order_id) {
    $o = wc_get_order($order_id);
    $lines = [];
    foreach ($o->get_items() as $item) {
        $product = $item->get_product();
        $lines[] = [
            'sku' => $product->get_sku(),
            'quantity' => $item->get_quantity(),
            'unit_price' => round(($item->get_total() + $item->get_total_tax()) / max($item->get_quantity(), 1), 2),
        ];
    }
    $pickup = $o->get_meta('_ke_pickup_branch'); // your checkout field
    $payload = [
        'external_id' => 'WC-' . $o->get_order_number(),
        'currency' => $o->get_currency(),
        'placed_at' => $o->get_date_created()->format(DATE_ATOM),
        'prices_include_tax' => true,
        'customer' => [
            'external_id' => (string) ($o->get_customer_id() ?: $o->get_billing_email()),
            'name' => trim($o->get_billing_first_name() . ' ' . $o->get_billing_last_name()),
            'company_name' => $o->get_billing_company() ?: null,
            'email' => $o->get_billing_email(),
            'phone' => $o->get_billing_phone(),
            'kra_pin' => $o->get_meta('_billing_kra_pin') ?: null,
        ],
        'fulfilment' => $pickup
            ? ['type' => 'pickup', 'pickup_location_id' => $pickup]
            : ['type' => 'delivery', 'shipping_fee' => (float) $o->get_shipping_total() + (float) $o->get_shipping_tax(),
               'carrier' => $o->get_shipping_method()],
        'shipping_address' => $pickup ? null : [
            'name' => trim($o->get_shipping_first_name() . ' ' . $o->get_shipping_last_name()),
            'phone' => $o->get_billing_phone(),
            'street' => $o->get_shipping_address_1(),
            'street2' => $o->get_shipping_address_2(),
            'city' => $o->get_shipping_city(),
            'county' => $o->get_shipping_state(),        // e.g. "KE-30" or "Nairobi"
            'postal_code' => $o->get_shipping_postcode(),
        ],
        'lines' => $lines,
        'note' => $o->get_customer_note(),
    ];
    try {
        $res = ke('POST', '/orders', $payload);
        $o->update_meta_data('_odoo_order_id', $res['data']['id']);
        $o->save();
    } catch (RuntimeException $e) {
        // insufficient_stock / validation errors: hold the order and alert staff
        $o->update_status('on-hold', 'Odoo: ' . $e->getMessage());
    }
});
```

### Response handling

- `201`: store `data.id` / `data.name` (S00042) on your order.
- `200` + `replayed`: already created (a retry) - same handling.
- `warnings[]` with `price_overridden`: your price differs from Odoo's; fix
  your catalog sync.
- `409 insufficient_stock`, `422 ...`: show/handle the message; nothing was
  created.

## 6. Payments

### Option A - the shop collects

After your gateway confirms the payment (server-side, never from the
browser redirect alone):

```js
await ke("POST", `/orders/WC-10023/payments`, {
  method: "mpesa",              // mpesa | airtel_money | card | bank_transfer | cash_on_delivery | other
  reference: "SJK3WEB001",      // M-Pesa code or gateway transaction id (unique)
  amount: 612,
  paid_at: new Date().toISOString(),
  phone: "0712345678",
});
```

Partial payments are allowed; when the total is covered Odoo invoices the
order (auto invoice) and transmits the invoice to KRA eTIMS. If the business
enabled *Require a matching Safaricom notification*, M-Pesa codes are
checked against what Safaricom reported to their till/paybill.

Cash on delivery: record the payment when the rider confirms collection
(`method: "cash_on_delivery"`, `reference`: rider receipt / delivery id).

### Option B - Odoo sends the STK push

```js
const { data: stk } = await ke("POST", `/orders/WC-10023/mpesa-stk-push`, { phone: "0712345678" });
// show "Check your phone and enter your M-Pesa PIN"
```

Then either wait for the `payment.received` / `payment.failed` webhook, or
poll `GET /mpesa/stk-requests/${stk.id}` every 3–5 seconds for up to
2 minutes. On `failed` / `cancelled`, show `message` ("The customer
cancelled the payment on their phone", "insufficient balance"...) and offer
to retry.

Amounts must be whole shillings (the business should enable KES 1.00
rounding; use integer prices for delivery fees).

## 7. Webhooks

Expose an HTTPS endpoint, verify the signature on the **raw body**, store
the event id to ignore duplicates, answer 200 fast, process asynchronously.

### Node.js (Express)

```js
import crypto from "node:crypto";
import express from "express";

const app = express();

app.post("/odoo-webhooks", express.raw({ type: "application/json" }), async (req, res) => {
  const parts = Object.fromEntries(
    (req.get("X-KE-Signature") || "").split(",").map((p) => p.split("=", 2))
  );
  const expected = crypto
    .createHmac("sha256", process.env.KE_WEBHOOK_SECRET)
    .update(`${parts.t}.`)
    .update(req.body)                       // raw Buffer
    .digest("hex");
  const valid =
    parts.v1?.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(parts.v1, "hex"), Buffer.from(expected, "hex")) &&
    Math.abs(Date.now() / 1000 - Number(parts.t)) < 300;
  if (!valid) return res.sendStatus(400);

  const event = JSON.parse(req.body.toString("utf8"));
  if (await alreadyProcessed(event.id)) return res.sendStatus(200);
  await queue.add(event);                   // process outside the request
  res.sendStatus(200);
});
```

### PHP

```php
$raw = file_get_contents('php://input');
$sig = [];
foreach (explode(',', $_SERVER['HTTP_X_KE_SIGNATURE'] ?? '') as $part) {
    [$k, $v] = array_pad(explode('=', $part, 2), 2, '');
    $sig[$k] = $v;
}
$expected = hash_hmac('sha256', ($sig['t'] ?? '') . '.' . $raw, getenv('KE_WEBHOOK_SECRET'));
if (!hash_equals($expected, $sig['v1'] ?? '') || abs(time() - (int) ($sig['t'] ?? 0)) > 300) {
    http_response_code(400);
    exit;
}
$event = json_decode($raw, true);
// dedupe on $event['id'], enqueue, then:
http_response_code(200);
```

### Python (Django / Flask)

```python
import hashlib, hmac, json, os, time

def verify_ke_signature(raw_body: bytes, header: str) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    expected = hmac.new(os.environ["KE_WEBHOOK_SECRET"].encode(),
                        f"{parts.get('t', '')}.".encode() + raw_body, hashlib.sha256).hexdigest()
    fresh = abs(time.time() - int(parts.get("t", 0))) < 300
    return fresh and hmac.compare_digest(expected, parts.get("v1", ""))

# Django view:
# if not verify_ke_signature(request.body, request.headers.get("X-KE-Signature", "")): return HttpResponse(status=400)
# event = json.loads(request.body)
```

### What to do with each event

| Event | Typical action in the shop |
|---|---|
| `order.confirmed` | Mark "processing". |
| `payment.received` | Mark paid (Option B), thank-you e-mail/SMS. |
| `payment.failed` | Show the reason, offer to retry the STK push. |
| `order.paid` | Fully paid. |
| `order.invoiced` / `invoice.etims_signed` | Show the invoice number, eTIMS CU number, QR code (`qr_data`) and the PDF link (`portal_url`) on the customer's order page / e-mail. |
| `order.fulfilled` | "Delivered" / "Collected". Partial deliveries show `partially_fulfilled`. |
| `order.cancelled` | Cancel; refund through your gateway if paid. |
| `stock.updated` | Update stock quantities. |
| `product.updated` | Update product (price, name, publication...). |

The event payload is a snapshot; when order matters, re-fetch
`GET /orders/{external_id}`.

## 8. Fulfilment

Fulfilment happens in Odoo: the warehouse picks, packs and **validates the
delivery order**, or the branch hands over a pickup order. You get
`order.fulfilled`. Status values for the customer page:

| `status` | Show |
|---|---|
| `pending` / `confirmed` | Order received |
| `ready` | Being prepared / ready for collection (pickup) |
| `partially_fulfilled` | Partially delivered |
| `fulfilled` | Delivered / collected |
| `cancelled` | Cancelled |

## 9. Invoices and KRA eTIMS

When the order is paid (auto invoice) Odoo posts the invoice and transmits
it to KRA eTIMS. Show the customer:

- invoice `number`,
- `etims.cu_invoice_number` and a QR code generated from `etims.qr_data`,
- a download link: `portal_url` (Odoo customer portal PDF, includes the
  eTIMS block).

`etims.status = to_send` means transmission in progress (usually seconds);
`training = true` means the business is still testing eTIMS.

## 10. Cancellations and returns

- Before delivery: `POST /orders/{id}/cancel` with a reason. Stock is
  released; a posted invoice is reversed with an eTIMS credit note.
- After delivery: returns are handled in Odoo (return + credit note); you
  will receive `order.invoiced` for the credit note.
- Refund money through your gateway / M-Pesa B2C; the business records the
  refund payment in Odoo.

## 11. Errors and retries

| Situation | Do |
|---|---|
| Timeout / 5xx / network error | Retry with backoff; same `external_id` / `reference` ⇒ no duplicates. |
| 429 | Wait `Retry-After`. Spread bulk syncs (≤ 2 req/s sustained). |
| 409 `insufficient_stock` | Tell the customer, refresh stock. |
| 422 | Fix the data (message + `details`); log `request_id`. |
| 401 | Key revoked/expired: alert the business. |

Every call is logged in Odoo (*E-commerce integration → API request logs*,
with request id, status and, for failures, the body) - use the request id
when asking the business for help.

## 12. Test plan (sandbox tenant)

Ask for a test tenant (e.g. `https://duka-test.pos.example.co.ke`) with
eTIMS in *training mode* and an M-Pesa *sandbox* account.

- [ ] `/ping` shows the test company.
- [ ] Full catalog import; images; unpublish a product in Odoo → `product.updated` (`active` false or gone).
- [ ] Change a price in Odoo → `product.updated` with the new price.
- [ ] Sell the last units in the POS → `stock.updated` within 5 minutes.
- [ ] Order with delivery + shipping fee; order with pickup; order with KRA PIN.
- [ ] Order for more than available → 409 handled in the UI.
- [ ] Re-send the same order → 200 `replayed`.
- [ ] Payment (Option A) → `payment.received`, `order.paid`, `order.invoiced`, `invoice.etims_signed` (training CU number).
- [ ] STK push (Option B) with the Daraja sandbox test number; cancel on the phone → `payment.failed`.
- [ ] Validate the delivery in Odoo → `order.fulfilled`.
- [ ] Cancel an unpaid and a paid order (credit note).
- [ ] Webhook signature rejection works (tamper with the body); duplicate event ignored.
- [ ] Stop your webhook endpoint for 10 minutes → events delivered after restart (retries).

## 13. Go-live checklist

- [ ] Production tenant URL, new production API key (scopes limited to what you use), webhook secret rotated.
- [ ] Webhook URL is HTTPS and reachable from the internet.
- [ ] Business switched eTIMS to the real connector (invoices no longer say *training*).
- [ ] M-Pesa account in production, C2B URLs registered (if codes are verified).
- [ ] Monitoring: alert on webhook failures (`GET /webhooks/events?state=failed`) and on repeated 4xx/5xx.
- [ ] API key and webhook secret stored in a secret manager, not in the repository.
