# Kenya Point of Sale - functional guide

For shop owners, branch managers and cashiers. It covers the one-time setup
of a tenant (business), daily operations at the till, M-Pesa, KRA eTIMS
receipts, refunds, end of day and reports. Technical details are in
[ARCHITECTURE.md](ARCHITECTURE.md).

Contents

1. [What you get](#1-what-you-get)
2. [One-time setup](#2-one-time-setup)
3. [Selling at the till](#3-selling-at-the-till)
4. [M-Pesa at the till](#4-m-pesa-at-the-till)
5. [eTIMS receipts](#5-etims-receipts)
6. [Customers, KRA PIN and invoices](#6-customers-kra-pin-and-invoices)
7. [Refunds and returns](#7-refunds-and-returns)
8. [Closing the till](#8-closing-the-till)
9. [Several branches](#9-several-branches)
10. [Reports](#10-reports)
11. [Cash purchases](#11-cash-purchases)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. What you get

| Need in a Kenyan shop | How it works |
|---|---|
| M-Pesa STK push ("Lipa na M-Pesa Online") | Cashier types the customer's number, the customer confirms with their PIN; the sale completes when Safaricom confirms. |
| Customer already paid to the till/paybill | Cashier types the M-Pesa code from the customer's SMS; Odoo checks it against Safaricom's notification and refuses codes already used. |
| KRA eTIMS | Every sale is transmitted to eTIMS; the receipt shows the CU invoice number, signature and QR code. Credit notes for refunds. |
| VAT-inclusive prices, 16% / 8% / 0% / exempt | Kenyan chart of accounts and taxes (`l10n_ke`), each tax mapped to an eTIMS tax type A–E. |
| No cents | KES 1.00 cash rounding. M-Pesa only accepts whole shillings. |
| Business customers need their PIN on the invoice | KRA PIN on the customer, validated, printed on receipts and sent to eTIMS. |
| Several branches | One point of sale (and warehouse) per branch; cashiers limited to their branch. |
| Data Protection Act | Phone numbers masked on receipts (`0712***678`); raw Safaricom payloads purged after 90 days. |

## 2. One-time setup

A new business (tenant) is created by the platform operator with
`scripts/tenant.sh create` ([MULTI_TENANCY.md](MULTI_TENANCY.md)). It already
has: Kenya as country, KES, the Kenyan chart of accounts, VAT-inclusive
prices, eTIMS in training mode and KES rounding. Then, as administrator:

### 2.1 Company

*Settings → Companies → your company*

- **KRA PIN** (the "Tax ID" field is labelled KRA PIN): `P051234567X`.
- Address, phone, logo (printed on receipts).

### 2.2 eTIMS

*Invoicing → Configuration → Settings → Kenya eTIMS*

| Field | Value |
|---|---|
| KRA eTIMS | enabled |
| eTIMS connector | *Training mode* until your OSCU/VSCU connector is installed (see [ETIMS.md](ETIMS.md)) |
| eTIMS environment | Sandbox, then Production |
| eTIMS branch ID | `00` for head office, `01`, `02`... for registered branches |
| Device serial, CU / SCU ID | from your eTIMS registration |

Use **Test connection**. In training mode every receipt says *TRAINING MODE -
NOT A VALID TAX INVOICE* and numbers start with `TRAIN`.

On products (*Accounting* tab → *KRA eTIMS*), fill the **item classification
code** when your connector requires it. Taxes are already mapped (16% → B,
8% → E, 0% → C, exempt → A); check *Invoicing → Configuration → Taxes →
Advanced options → eTIMS tax type* if you add taxes.

### 2.3 M-Pesa

1. On the [Safaricom Developer Portal](https://developer.safaricom.co.ke)
   create an app with *Lipa Na M-Pesa Sandbox* (later: go-live for your till
   or paybill). Note the consumer key/secret and the passkey.
2. *Point of Sale → Configuration → M-Pesa accounts → New*
   - Number type: **Buy Goods (Till)** or **Paybill**.
   - Business shortcode: the paybill number, or for a till the store number
     that owns it (Safaricom calls it "HO" / store number).
   - Till number (Buy Goods only): the number customers pay to.
   - Consumer key, consumer secret, passkey.
   - Click **Test credentials**.
3. Click **Register C2B URLs** (needed for "verify M-Pesa code").
4. Click **Create POS payment method**: this creates an *M-Pesa* bank
   journal and payment method connected to the account.

Full details, go-live and troubleshooting: [MPESA_SETUP.md](MPESA_SETUP.md).

### 2.4 Point of Sale

*Point of Sale → Configuration → Settings* (select the shop at the top):

- **Payment methods**: Cash + M-Pesa (+ Card, Customer account...).
- **Cash rounding**: enabled, *KES 1.00 (Kenya)*, not only for cash.
- **Kenya** block:
  - *Lipa na M-Pesa on receipts*: filled from the M-Pesa account
    ("Lipa na M-Pesa - Buy Goods Till No. 5123456"), edit if needed.
  - *Print eTIMS details on receipts*: keep enabled.
  - *Allow forcing M-Pesa payments*: keep disabled.
- **Receipt header/footer**: e.g. "Asante kwa kununua! Goods once sold are
  not returnable."
- **Invoice journal**: needed to print VAT invoices for business customers.

Inventory: set *Point of Sale → Configuration → Settings → Inventory →
Update quantities: in real time* when you also sell online, so online stock
reflects shop sales immediately.

### 2.5 Users

*Settings → Users*

- Cashiers: *Kenya retail role: Cashier*, and in **Allowed shops** the
  branch(es) they work at.
- Branch managers: *Kenya retail role: Branch Manager* (POS administrator,
  inventory, purchases).
- Accountants: *Invoicing* rights; *M-Pesa / Administrator* to manage M-Pesa
  accounts.

## 3. Selling at the till

1. *Point of Sale → Dashboard → Open Register* on your shop. Count the
   opening float.
2. Scan or tap products. Prices include VAT.
3. *Customer* (optional): pick or create the customer. For a business
   customer enter the **KRA PIN** (validated: `A` or `P`, 9 digits, a letter).
4. **Payment**: choose Cash, M-Pesa, Card... Split payments are allowed
   (e.g. KES 500 cash + rest by M-Pesa).
5. **Validate**: the order is saved, transmitted to eTIMS and the receipt
   prints with the CU invoice number and QR code.

## 4. M-Pesa at the till

Selecting **M-Pesa** opens the M-Pesa window, with two options.

### Send STK push

1. Type the customer's Safaricom number (`0712 345 678`; pre-filled from the
   customer). Adjust the amount for a split payment (whole shillings).
2. **Send request**. The customer gets a prompt and enters their PIN.
3. The window waits for Safaricom. When confirmed it closes, the line is
   marked paid and a notification shows the M-Pesa code.

If the customer cancels, has insufficient balance, enters a wrong PIN or the
phone is unreachable, the window explains why. Press **Send again** or switch
to **Enter M-Pesa code**.

### Enter M-Pesa code

For customers who paid to your till/paybill from their phone:

1. Ask for the code at the start of their M-Pesa SMS (`SJK3ABCD9Z`).
2. Type it and **Verify code**. Odoo checks that Safaricom reported this
   payment to your till and that it was not used for another sale.
3. The line takes the amount actually paid. If the customer paid more, the
   difference is change (paid in cash).

"No M-Pesa payment ... has reached your till yet": wait a few seconds (the
notification can take up to a minute), check the code and that the customer
paid to the right till.

### Rules that protect you

- A payment line is never marked paid without Safaricom's confirmation;
  *Force done* is blocked for M-Pesa unless a manager enables it.
- An M-Pesa code can pay only one sale.
- If a customer pays after the cashier gave up waiting, the payment appears in
  *Point of Sale → Orders → M-Pesa payments* with the filter **Unclaimed
  payments**, and can still be used with "Enter M-Pesa code".

## 5. eTIMS receipts

Each receipt carries a **KRA eTIMS** block: CU invoice number, CU serial,
internal data, receipt signature, signing time and the QR code customers can
scan to verify the receipt on KRA's portal.

If KRA does not answer (network, KRA downtime), the receipt prints
*"Transmission to KRA pending"* and Odoo keeps retrying in the background
(every 5 minutes). Follow-up: *Point of Sale → Orders → Orders*, filter
**eTIMS pending** / **eTIMS errors**; the order's *eTIMS* tab shows the
error and a **Send to eTIMS** button. Reprint the receipt once signed.

## 6. Customers, KRA PIN and invoices

- A customer who needs a tax invoice in their name must have a valid KRA PIN.
- Tick **Invoice** on the payment screen: Odoo creates the invoice, which
  becomes the fiscal document sent to eTIMS (the sale is not transmitted
  twice). The invoice PDF shows the eTIMS block and QR code.
- Walk-in customers do not need a PIN.

## 7. Refunds and returns

*Orders → (select the order) → Refund*, choose the products and quantities,
then pay back (cash, or M-Pesa reversal done on the M-Pesa portal and
recorded in cash/bank). The refund is transmitted to eTIMS as a **credit
note** that references the original receipt number.

Signed invoices cannot be reset to draft: issue a credit note.

## 8. Closing the till

*Close Register*: count the cash, check the M-Pesa total against the M-Pesa
statement (*Point of Sale → Orders → M-Pesa payments*, filter *Today*),
then close. Odoo posts the session accounting entries.

Before closing, check *Orders → filter eTIMS pending*: anything listed is
transmitted automatically when KRA is reachable.

## 9. Several branches

- One **point of sale** per branch, ideally with its own **warehouse** and,
  if each branch has its own till, its own M-Pesa account and payment method.
- If branches are registered separately with KRA (different branch ids),
  create a **branch company** per branch (*Settings → Companies*, parent =
  head office) with its own eTIMS branch ID. Each company keeps its own
  sales, stock, M-Pesa and eTIMS configuration.
- Restrict cashiers with **Allowed shops** on their user.

## 10. Reports

*Point of Sale → Reporting → Kenya*

- **Daily collections**: amounts collected per day and payment method (cash
  vs M-Pesa vs card), per shop. Days follow East Africa Time.
- **VAT by eTIMS category**: taxable sales and VAT per tax type (A exempt,
  B 16%, C zero rated, D non-VAT, E 8%) - the figures needed for the VAT3
  return.

Standard Odoo reports (sales details, session reports, inventory valuation)
remain available.

## 11. Cash purchases

*Purchase → Orders → Cash purchase*: supplier, items, quantities, prices, the
cash box (or M-Pesa journal) it was paid from. Odoo creates the purchase
order, receives the goods, posts the vendor bill and pays it, all linked.

Tick **Supplier issued an eTIMS invoice** and enter its number to claim input
VAT; without it the purchase is recorded without VAT, as KRA only accepts
input VAT backed by an eTIMS invoice.

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "M-Pesa only accepts whole shillings" | Enable *KES 1.00* cash rounding (not only for cash), or adjust the amount. |
| STK push: "Could not reach M-Pesa" | Internet down or Daraja unavailable. Use "Enter M-Pesa code" (customer pays to the till) or another payment method. |
| STK push fails immediately with "Invalid ..." | Wrong credentials/shortcode/passkey or sandbox vs production mix-up: *Test credentials* on the M-Pesa account. |
| Waiting forever for the STK result | Safaricom cannot reach your server's callback URL. Odoo falls back on status queries after 15 s; check the public HTTPS address in the M-Pesa account (*Callbacks* tab). |
| "No M-Pesa payment ... has reached your till" | C2B URLs not registered (*Register C2B URLs*), wrong till, or the notification is still on its way. |
| "already been used" | The code paid another sale. Check *M-Pesa payments*; a manager can **Release** a payment reserved by an abandoned sale. |
| Receipt says "Transmission to KRA pending" | KRA not reachable: Odoo retries automatically; reprint later. |
| KRA PIN refused | Format must be `A` or `P`, 9 digits, a letter (`P051234567X`). |
