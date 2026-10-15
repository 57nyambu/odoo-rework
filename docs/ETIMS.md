# KRA eTIMS

Module: `ke_etims` (+ POS integration in `ke_pos`). This page explains the
behaviour, the configuration, and how to plug a real OSCU/VSCU connector.

## 1. What is fiscalized

| Document | When | Notes |
|---|---|---|
| Customer invoice (`out_invoice`) | When posted | Buyer KRA PIN sent when known. |
| Credit note (`out_refund`) | When posted | References the original invoice's eTIMS receipt number. |
| POS order (receipt) | When paid | Unless invoiced: the invoice is then the fiscal document. |
| POS refund | When paid | Sent as a credit note referencing the refunded receipt. |

Signed documents cannot go back to draft.

## 2. Configuration

*Invoicing → Configuration → Settings → Kenya eTIMS* (per company):

| Setting | Notes |
|---|---|
| KRA eTIMS | Enables transmission for this company. |
| eTIMS connector | Implementation to use. `Training mode` is built in. Connector modules add theirs. |
| eTIMS environment | Sandbox / Production. Training mode is refused in production. |
| eTIMS branch ID | `bhfId`: `00` head office, `01`... branches. |
| Device serial | `dvcSrlNo` of the registered OSCU/VSCU. |
| CU / SCU ID | Control unit id returned at initialisation, printed on receipts. |

Taxes: *eTIMS tax type* on each sales tax (computed automatically):

| Code | Category | Default mapping |
|---|---|---|
| A | Exempt | 0% taxes named "exempt" |
| B | VAT 16% | 16% |
| C | Zero rated | other 0% taxes (exports...) |
| D | Non-VAT | lines without a VAT tax |
| E | VAT 8% | 8% |

Withholding taxes and levies (e.g. 2% catering levy) have no tax type and
are ignored for the category.

Products (*Accounting* tab): item classification code, product type
(raw material / finished product / service), packaging unit (default `NT`),
quantity unit (default `U`), country of origin, and the eTIMS item code once
registered by the connector.

## 3. Transmission queue

States: `not_applicable` → `to_send` → `sent` (or `error` → retried).

- Posting an invoice / paying a POS order marks it `to_send` and wakes the
  cron *Kenya eTIMS: transmit queued documents* (also every 5 minutes).
- Each transmission runs in a savepoint; a failure stores the error, counts
  the attempt and waits 5 minutes before the next try, up to *eTIMS max
  retries* (12 by default). After that, fix the cause and use **Send to
  eTIMS** (button on the document, or the list action).
- Rows are locked (`FOR UPDATE SKIP LOCKED`) during transmission: the cron,
  the POS and users can never sign a document twice.
- The POS signs immediately after saving the order to print the details; on
  failure the receipt says "Transmission to KRA pending".

Monitoring: filters **eTIMS pending** / **eTIMS errors** on invoices and POS
orders; *Settings → Kenya eTIMS → Pending transmissions*.

## 4. Writing a connector

A connector is a separate module that depends on `ke_etims`:

```python
# __manifest__.py
{"name": "Kenya eTIMS - ACME OSCU", "depends": ["ke_etims"], ...}
```

```python
from odoo import fields, models
from odoo.exceptions import UserError
import requests


class ResCompany(models.Model):
    _inherit = "res.company"

    ke_etims_connector = fields.Selection(
        selection_add=[("acme_oscu", "ACME OSCU")],
        ondelete={"acme_oscu": "set default"},
    )
    acme_api_key = fields.Char(groups="base.group_system")


class KeEtimsService(models.AbstractModel):
    _inherit = "ke.etims.service"

    def _submit_acme_oscu(self, document, payload):
        company = document.company_id
        try:
            response = requests.post(
                "https://oscu.acme.co.ke/v1/sales",
                json=self._acme_translate(payload),      # map to the vendor/KRA format
                headers={"Authorization": f"Bearer {company.acme_api_key}"},
                timeout=(5, 8),                          # keep it short: the POS waits for it
            )
            response.raise_for_status()
        except requests.RequestException as error:
            raise UserError(f"eTIMS unreachable: {error}") from error
        data = response.json()
        return {
            "invoice_number": f"{data['sdcId']}/{data['rcptNo']}",   # required
            "scu_id": data["sdcId"],
            "receipt_number": data["rcptNo"],
            "internal_data": data["intrlData"],
            "signature": data["rcptSign"],
            "signed_at": fields.Datetime.now(),
            "qr_data": data["qrUrl"],          # string encoded in the QR code
            "raw_response": data,
        }

    def _test_connection_acme_oscu(self, company):   # optional, settings button
        return "Connected to ACME OSCU."
```

Rules:

- Raise `UserError` (or any exception) on failure: the queue records it and
  retries. Do not catch-and-return partial results.
- Be idempotent if the vendor supports it (send `payload["reference"]` as the
  invoice number so a retry after a timeout does not create a second receipt).
- Keep timeouts short (≤ 10 s total): the POS waits for the answer before
  printing.

### Payload contract

`_ke_etims_prepare_payload()` builds the same structure for invoices and POS
orders:

```json
{
  "document_type": "sale | credit_note",
  "source": "invoice | pos",
  "reference": "INV/2026/00042 | Shop/0001-001-0042",
  "original_reference": 152,
  "reason": "Damaged goods",
  "date": "2026-10-15 07:19:00",
  "seller": {"pin": "P051234567X", "name": "Duka Bora Ltd", "branch_code": "00",
             "device_serial": "...", "scu_id": "KRACU0100000001"},
  "buyer": {"pin": "A012345678Z", "name": "Achieng Otieno", "phone": "+254712345678", "email": null},
  "currency": "KES",
  "exchange_rate": 1.0,
  "payment_type": "cash | credit | cash_credit | cheque | card | mobile_money | other",
  "lines": [
    {
      "sequence": 1,
      "item_code": "KE2NTU0000001",
      "item_class_code": "5020230100",
      "barcode": "5449000000996",
      "name": "Coca-Cola 500ml",
      "product_type": "2",
      "packaging_unit_code": "NT",
      "quantity_unit_code": "U",
      "origin_country": "KE",
      "quantity": 2.0,
      "unit_price": 70.0,
      "discount_amount": 0.0,
      "taxable_amount": 120.69,
      "tax_type": "B",
      "tax_rate": 16.0,
      "tax_amount": 19.31,
      "total_amount": 140.0
    }
  ],
  "totals": {
    "taxable_A": 0.0, "tax_A": 0.0, "taxable_B": 120.69, "tax_B": 19.31,
    "taxable_C": 0.0, "tax_C": 0.0, "taxable_D": 0.0, "tax_D": 0.0,
    "taxable_E": 0.0, "tax_E": 0.0,
    "total_taxable": 120.69, "total_tax": 19.31, "total_amount": 140.0
  }
}
```

- Amounts are positive for both sales and credit notes (the document type
  carries the sign). `unit_price` is VAT inclusive, before discount.
- KRA payment type codes (`pmtTyCd`) for each value are in
  `ETIMS_PAYMENT_TYPE_CODES` (`01` cash ... `06` mobile money, `07` other);
  POS payment methods carry an *eTIMS payment type* setting.

## 5. Going live checklist

- [ ] Company KRA PIN, branch ID, device serial, CU/SCU ID filled.
- [ ] OSCU/VSCU connector installed and selected; environment *Production*.
- [ ] Items registered with KRA (item classification codes on products).
- [ ] Taxes have the right eTIMS tax type.
- [ ] A test sale, a test invoice and a test credit note are `Sent`, and the
      QR code opens the receipt on KRA's verification page.
- [ ] Monitoring: someone checks *eTIMS errors* daily.
