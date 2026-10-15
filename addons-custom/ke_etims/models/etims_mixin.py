import base64
import io
import json
import logging
from collections import defaultdict

import qrcode

from odoo import fields, models
from odoo.tools import SQL, float_round

from .account_tax import ETIMS_TAX_RATES, ETIMS_TAX_TYPES

_logger = logging.getLogger(__name__)

ETIMS_STATES = [
    ("not_applicable", "Not applicable"),
    ("to_send", "To send"),
    ("sent", "Sent"),
    ("error", "Error"),
]


class KeEtimsMixin(models.AbstractModel):
    """Fiscal document behaviour shared by invoices and POS orders.

    Concrete models implement:

    * ``_ke_etims_is_applicable()`` - should this record be fiscalized?
    * ``_ke_etims_prepare_payload()`` - the normalized payload sent to the
      connector (see ``_ke_etims_build_payload`` for the shape).
    """

    _name = "ke.etims.mixin"
    _description = "Kenya eTIMS fiscal document"

    ke_etims_state = fields.Selection(
        ETIMS_STATES,
        string="eTIMS status",
        default="not_applicable",
        copy=False,
        index=True,
        readonly=True,
    )
    ke_etims_invoice_number = fields.Char(
        string="CU invoice number",
        copy=False,
        readonly=True,
        help="Control unit invoice number assigned by KRA eTIMS (printed on the document).",
    )
    ke_etims_scu_id = fields.Char(string="CU serial / SCU ID", copy=False, readonly=True)
    ke_etims_receipt_number = fields.Integer(string="eTIMS receipt number", copy=False, readonly=True)
    ke_etims_internal_data = fields.Char(string="eTIMS internal data", copy=False, readonly=True)
    ke_etims_signature = fields.Char(string="eTIMS receipt signature", copy=False, readonly=True)
    ke_etims_signed_at = fields.Datetime(string="eTIMS signed at", copy=False, readonly=True)
    ke_etims_qr_data = fields.Char(string="eTIMS QR data", copy=False, readonly=True)
    ke_etims_training = fields.Boolean(
        string="eTIMS training document",
        copy=False,
        readonly=True,
        help="Signed by the training connector: NOT a valid tax invoice.",
    )
    ke_etims_error = fields.Text(string="eTIMS error", copy=False, readonly=True)
    ke_etims_attempts = fields.Integer(string="eTIMS attempts", copy=False, readonly=True)
    ke_etims_last_attempt_at = fields.Datetime(string="eTIMS last attempt", copy=False, readonly=True)
    ke_etims_raw_response = fields.Text(
        string="eTIMS raw response", copy=False, readonly=True, groups="base.group_system"
    )

    # ------------------------------------------------------------------
    # To implement
    # ------------------------------------------------------------------
    def _ke_etims_is_applicable(self):
        self.ensure_one()
        return False

    def _ke_etims_prepare_payload(self):
        raise NotImplementedError()

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------
    def _ke_etims_queue(self):
        """Flag applicable documents for transmission and wake the sender.

        Transmission happens in its own transaction (cron) so a slow or
        unavailable KRA endpoint never blocks posting an invoice or closing
        a sale, and a rolled-back transaction can never leave a document
        signed at KRA but missing in Odoo.
        """
        todo = self.filtered(
            lambda d: d.ke_etims_state == "not_applicable" and d._ke_etims_is_applicable()
        )
        if todo:
            todo.write({"ke_etims_state": "to_send", "ke_etims_error": False})
            cron = self.env.ref("ke_etims.ir_cron_ke_etims_send", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return todo

    def _ke_etims_lock_for_sending(self):
        """Row-lock documents still waiting for transmission; documents
        locked by a concurrent transmission (cron vs. button vs. POS) are
        skipped so nothing is ever signed twice."""
        if not self:
            return self
        self.flush_recordset(["ke_etims_state"])
        self.env.cr.execute(
            SQL(
                "SELECT id FROM %s WHERE id IN %s AND ke_etims_state IN ('to_send', 'error') "
                "FOR UPDATE SKIP LOCKED",
                SQL.identifier(self._table),
                tuple(self.ids),
            )
        )
        locked = self.browse([row[0] for row in self.env.cr.fetchall()])
        locked.invalidate_recordset(["ke_etims_state"])
        return locked

    def _ke_etims_send(self):
        """Transmit documents to eTIMS. Never raises: failures are stored on
        the document and retried by the cron.

        :return: tuple (sent, failed) recordsets
        """
        sent = failed = self.browse()
        service = self.env["ke.etims.service"]
        for doc in self._ke_etims_lock_for_sending():
            doc = doc.with_company(doc.company_id)
            try:
                with self.env.cr.savepoint():
                    payload = doc._ke_etims_prepare_payload()
                    result = service.with_company(doc.company_id).submit(doc, payload)
                    doc._ke_etims_apply_result(result)
                sent |= doc
            except Exception as error:  # noqa: BLE001 - a connector failure must not break the queue
                _logger.warning("eTIMS transmission failed for %s: %s", doc, error)
                doc.write(
                    {
                        "ke_etims_state": "error",
                        "ke_etims_error": str(error),
                        "ke_etims_attempts": doc.ke_etims_attempts + 1,
                        "ke_etims_last_attempt_at": fields.Datetime.now(),
                    }
                )
                failed |= doc
        return sent, failed

    def _ke_etims_apply_result(self, result):
        self.ensure_one()
        self.write(
            {
                "ke_etims_state": "sent",
                "ke_etims_invoice_number": result["invoice_number"],
                "ke_etims_scu_id": result.get("scu_id") or self.company_id.ke_etims_scu_id,
                "ke_etims_receipt_number": result.get("receipt_number") or 0,
                "ke_etims_internal_data": result.get("internal_data"),
                "ke_etims_signature": result.get("signature"),
                "ke_etims_signed_at": result.get("signed_at") or fields.Datetime.now(),
                "ke_etims_qr_data": result.get("qr_data"),
                "ke_etims_training": bool(result.get("training")),
                "ke_etims_error": False,
                "ke_etims_attempts": self.ke_etims_attempts + 1,
                "ke_etims_last_attempt_at": fields.Datetime.now(),
                "ke_etims_raw_response": json.dumps(result.get("raw_response"), default=str),
            }
        )
        if hasattr(self, "_message_log"):
            self._message_log(
                body=self.env._(
                    "Transmitted to KRA eTIMS. CU invoice number: %s", result["invoice_number"]
                )
            )

    def action_ke_etims_send(self):
        """Button: transmit now and report the outcome."""
        to_send = self.filtered(lambda d: d.ke_etims_state in ("to_send", "error"))
        sent, failed = to_send._ke_etims_send()
        if failed:
            message = "\n".join(f"{doc.display_name}: {doc.ke_etims_error}" for doc in failed)
            notif_type = "danger"
        elif sent:
            message = self.env._("%s document(s) transmitted to eTIMS.", len(sent))
            notif_type = "success"
        else:
            message = self.env._("Nothing to transmit.")
            notif_type = "info"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("eTIMS"),
                "message": message,
                "type": notif_type,
                "sticky": bool(failed),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_ke_etims_retry(self):
        """Reset the retry counter of documents in error and re-queue them."""
        docs = self.filtered(lambda d: d.ke_etims_state == "error")
        docs.write({"ke_etims_attempts": 0, "ke_etims_last_attempt_at": False, "ke_etims_state": "to_send"})
        cron = self.env.ref("ke_etims.ir_cron_ke_etims_send", raise_if_not_found=False)
        if cron and docs:
            cron.sudo()._trigger()
        return True

    def _ke_etims_qr_code_data_uri(self):
        """PNG data URI of the eTIMS QR code, for reports and the portal."""
        self.ensure_one()
        if not self.ke_etims_qr_data:
            return False
        image = qrcode.make(self.ke_etims_qr_data, box_size=4, border=1)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------
    def _ke_etims_seller_vals(self):
        company = self.company_id
        return {
            "pin": company.vat,
            "name": company.name,
            "branch_code": company.ke_etims_branch_code or "00",
            "device_serial": company.ke_etims_device_serial,
            "scu_id": company.ke_etims_scu_id,
        }

    def _ke_etims_buyer_vals(self, partner):
        partner = partner.commercial_partner_id if partner else partner
        if not partner:
            return {"pin": None, "name": None, "phone": None, "email": None}
        pin = partner.vat if partner.country_id.code in (False, "KE") else None
        return {
            "pin": pin or None,
            "name": partner.name,
            "phone": partner.phone or None,
            "email": partner.email or None,
        }

    def _ke_etims_build_payload(self, *, document_type, source, reference, date, partner, currency,
                                exchange_rate, lines, payment_type, original=None, reason=None):
        """Assemble the normalized payload handed to connectors.

        ``lines`` is a list of dicts with: product (product.product),
        name, quantity, unit_price (tax included), discount_amount,
        taxable_amount, tax_amount, total_amount, taxes (account.tax).
        """
        Tax = self.env["account.tax"]
        precision = currency.decimal_places
        totals = defaultdict(float)
        payload_lines = []
        for sequence, line in enumerate(lines, start=1):
            product = line["product"]
            template = product.product_tmpl_id
            tax_type = Tax._ke_etims_tax_type_for(line["taxes"])
            taxable = float_round(line["taxable_amount"], precision_digits=precision)
            tax_amount = float_round(line["tax_amount"], precision_digits=precision)
            total = float_round(line["total_amount"], precision_digits=precision)
            totals[f"taxable_{tax_type}"] += taxable
            totals[f"tax_{tax_type}"] += tax_amount
            totals["total_taxable"] += taxable
            totals["total_tax"] += tax_amount
            totals["total_amount"] += total
            payload_lines.append(
                {
                    "sequence": sequence,
                    "item_code": template.ke_etims_item_code or product.default_code or None,
                    "item_class_code": template.ke_etims_item_class_code or None,
                    "barcode": product.barcode or None,
                    "name": line["name"],
                    "product_type": template.ke_etims_product_type or "2",
                    "packaging_unit_code": template.ke_etims_packaging_unit or "NT",
                    "quantity_unit_code": template.ke_etims_quantity_unit or "U",
                    "origin_country": (template.ke_etims_origin_country_id.code or "KE").upper(),
                    "quantity": line["quantity"],
                    "unit_price": line["unit_price"],
                    "discount_amount": float_round(line["discount_amount"], precision_digits=precision),
                    "taxable_amount": taxable,
                    "tax_type": tax_type,
                    "tax_rate": ETIMS_TAX_RATES[tax_type],
                    "tax_amount": tax_amount,
                    "total_amount": total,
                }
            )
        for code, _label in ETIMS_TAX_TYPES:
            totals.setdefault(f"taxable_{code}", 0.0)
            totals.setdefault(f"tax_{code}", 0.0)
        return {
            "document_type": document_type,
            "source": source,
            "reference": reference,
            "original_reference": original,
            "reason": reason,
            "date": fields.Datetime.to_string(date) if hasattr(date, "hour") else fields.Date.to_string(date),
            "seller": self._ke_etims_seller_vals(),
            "buyer": self._ke_etims_buyer_vals(partner),
            "currency": currency.name,
            "exchange_rate": exchange_rate,
            "payment_type": payment_type,
            "lines": payload_lines,
            "totals": {key: float_round(value, precision_digits=precision) for key, value in totals.items()},
        }
