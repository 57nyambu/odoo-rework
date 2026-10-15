# M-Pesa (Safaricom Daraja) setup

How to connect a till or paybill to Odoo, test in the sandbox and go live.
Module: `ke_mpesa` (used by `ke_pos` and `ke_ecommerce_api`).

## 1. Concepts

| Term | Meaning |
|---|---|
| **Paybill** | Business number + account number. STK `TransactionType = CustomerPayBillOnline`, `PartyB` = paybill. |
| **Buy Goods (Till)** | Till number customers pay to. Belongs to a *store / head office* number used as `BusinessShortCode` for STK. `TransactionType = CustomerBuyGoodsOnline`, `PartyB` = till. |
| **STK push** (Lipa na M-Pesa Online) | Odoo asks Safaricom to show a PIN prompt on the customer's phone. Result arrives on the *STK callback URL*. |
| **C2B** | Customer pays from their phone (M-Pesa menu / app). Safaricom calls the *validation* then *confirmation* URLs registered for the shortcode. |
| **Consumer key / secret** | Credentials of your Daraja app (OAuth). |
| **Passkey** | Lipa na M-Pesa Online passkey of the shortcode (sandbox: published by Safaricom; production: e-mailed at go-live). |

## 2. Sandbox

1. Create an account on <https://developer.safaricom.co.ke>, create an app
   with *Lipa Na M-Pesa Sandbox* and *M-Pesa Sandbox* products.
2. In Odoo: *Point of Sale → Configuration → M-Pesa accounts → New* (or
   *Invoicing → Configuration → M-Pesa accounts*):

   | Field | Sandbox value |
   |---|---|
   | Environment | Sandbox |
   | Number type | Paybill |
   | Business shortcode | `174379` |
   | Consumer key / secret | from your Daraja app |
   | Passkey | the sandbox passkey shown in the Daraja simulator for 174379 |

3. **Test credentials** must say *Daraja credentials are valid*.
4. Your Odoo must be reachable over **HTTPS from the internet** for callbacks.
   Locally, use a tunnel (ngrok, cloudflared) and put its address in
   *Callbacks → Callback base URL*. The *Callbacks* tab shows the exact URLs:

   ```
   https://<tenant host>/ke/lipa/stk/<secret>
   https://<tenant host>/ke/lipa/c2b/confirm/<secret>
   https://<tenant host>/ke/lipa/c2b/validate/<secret>
   ```

5. **Register C2B URLs** (sandbox C2B can be simulated from the Daraja portal
   with the test MSISDN).
6. **Create POS payment method** → add it to your shop.

## 3. Going live

1. Apply for go-live on the Daraja portal with your production shortcode.
   You receive production consumer key/secret (new app) and the passkey.
2. Create a **new M-Pesa account** in Odoo (Environment: Production) - keep
   the sandbox one archived for testing.
3. Test credentials, **Register C2B URLs**, create/attach the POS payment
   method, and run a KES 1 transaction.
4. Optional hardening: restrict callbacks to Safaricom's IP ranges with the
   system parameter `ke_mpesa.allowed_callback_ips` (comma-separated IPs or
   CIDRs, *Settings → Technical → System parameters*), and/or in nginx. Get
   the current ranges from Safaricom (they change); a wrong list blocks all
   payments.

## 4. Callback security

Daraja does not sign callbacks. Odoo therefore:

- puts a 48-character random **secret** at the end of each callback URL
  (hex only, because Daraja refuses URLs containing words such as `exe`,
  `cmd`, `sql`, `query`, `mpesa`, `safaricom`);
- only changes transactions in server code: a callback can confirm a pending
  STK request or record a C2B payment, nothing else;
- can enforce an IP allow-list (above).

If a secret leaks: **New callback URLs**, then **Register C2B URLs** again.
STK requests always use the current URL.

## 5. Transaction states

| State | Meaning |
|---|---|
| Waiting for customer | STK prompt sent, no answer yet. |
| Confirmed | Money received (callback, status query, or C2B confirmation). |
| Failed / Cancelled | Customer cancelled (1032), wrong PIN (2001), insufficient funds (1), unreachable (1037), timeout... |
| Expired | No answer from Safaricom after 10 minutes (status queried every 2 minutes first). |

A confirmed payment is **claimed** when used by a POS order or an online
order (*Usage* group on the transaction). Unclaimed confirmed payments
(customer paid but the sale was abandoned) are listed with the filter
**Unclaimed payments** and can be used later with "Enter M-Pesa code".
A manager can **Release** a payment reserved by a sale that never completed.

## 6. Accounting

Each M-Pesa POS payment method books to an **M-Pesa bank journal** (created
by *Create POS payment method*). At session close Odoo posts the M-Pesa
total to the journal's outstanding account; reconcile it with the M-Pesa
statement (import the statement or use bank synchronisation) like any bank
account. Safaricom charges for paybills are bank fees on that journal.

## 7. Data protection

Phone numbers are stored normalised (`2547XXXXXXXX`) for matching and shown
masked (`0712***678`) on receipts and lists. Raw callback payloads (which
contain names and numbers) are visible to M-Pesa administrators only and are
deleted after 90 days (`ke_mpesa.raw_payload_retention_days`). Structured
transaction data is kept for the accounting retention period.

## 8. Result codes

| Code | Shown to the cashier |
|---|---|
| 0 | Payment successful |
| 1 | Insufficient M-Pesa balance |
| 1001 | Customer has another transaction in progress |
| 1019 | Request expired |
| 1025 / 9999 | M-Pesa could not process / send the prompt |
| 1032 | Cancelled by the customer |
| 1037 | Phone unreachable |
| 2001 | Wrong PIN |

## 9. Troubleshooting

| Problem | Check |
|---|---|
| *Test credentials* fails | Key/secret of the right app; environment matches (sandbox keys don't work in production). |
| STK: "Invalid Access Token" | Odoo refreshes automatically once; persistent → credentials revoked. |
| STK: "Bad Request - Invalid BusinessShortCode/PartyB" | Till vs paybill type; for tills the *business shortcode* is the store number, the *till number* is separate. |
| STK: "Invalid CallBackURL" | The callback base URL must be public HTTPS and must not contain forbidden words (e.g. a domain containing "mpesa"). |
| STK prompt appears but Odoo keeps waiting | Callback not reaching Odoo (firewall, tunnel down). Odoo still confirms through status queries after 15 s. |
| C2B payments not arriving | URLs not registered, or registered for another shortcode; production C2B registration is per shortcode. |
