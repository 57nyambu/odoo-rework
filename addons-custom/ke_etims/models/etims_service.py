import hashlib
import json
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Normalized payment types used in payloads, with the KRA eTIMS payment type
# code (pmtTyCd) connectors should send for each.
ETIMS_PAYMENT_TYPE_CODES = {
    "cash": "01",
    "credit": "02",
    "cash_credit": "03",
    "cheque": "04",
    "card": "05",
    "mobile_money": "06",
    "other": "07",
}

# Keys a connector must return from `_submit_<connector>()`.
RESULT_REQUIRED_KEYS = ("invoice_number",)

RETRY_DELAY_MINUTES = 5


class KeEtimsService(models.AbstractModel):
    """Single entry point between Odoo documents and KRA eTIMS.

    Documents (invoices, POS orders) never talk to KRA themselves: they build
    a connector-agnostic payload (see ``ke.etims.mixin._ke_etims_prepare_payload``)
    and hand it to :meth:`submit`, which dispatches to the connector selected
    on the company (``res.company.ke_etims_connector``).

    Adding a connector (OSCU, VSCU, a vendor middleware...) is done in a
    separate module::

        class ResCompany(models.Model):
            _inherit = "res.company"
            ke_etims_connector = fields.Selection(
                selection_add=[("acme_oscu", "ACME OSCU")],
                ondelete={"acme_oscu": "set default"})

        class KeEtimsService(models.AbstractModel):
            _inherit = "ke.etims.service"
            def _submit_acme_oscu(self, document, payload):
                ...call the API...
                return {"invoice_number": ..., "scu_id": ..., ...}
    """

    _name = "ke.etims.service"
    _description = "Kenya eTIMS gateway"

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def submit(self, document, payload):
        """Transmit one fiscal document.

        :param document: record using ``ke.etims.mixin``
        :param dict payload: normalized payload (see module docs)
        :return: dict with at least ``invoice_number``; optional keys:
            ``scu_id``, ``receipt_number``, ``internal_data``, ``signature``,
            ``signed_at`` (datetime), ``qr_data`` (string encoded in the QR
            code), ``training`` (bool), ``raw_response`` (any JSON-able).
        :raise UserError: when the document cannot be transmitted.
        """
        company = document.company_id
        if not company.ke_etims_enabled:
            raise UserError(self.env._("eTIMS is not enabled for %s.", company.display_name))
        if not company.vat:
            raise UserError(
                self.env._("Set the KRA PIN of %s before transmitting to eTIMS.", company.display_name)
            )
        connector = company.ke_etims_connector
        handler = getattr(self, f"_submit_{connector}", None)
        if not connector or not handler:
            raise UserError(
                self.env._(
                    "No implementation found for the eTIMS connector '%s'. Install the "
                    "matching connector module.",
                    connector,
                )
            )
        result = handler(document, payload)
        missing = [key for key in RESULT_REQUIRED_KEYS if not result.get(key)]
        if missing:
            raise UserError(
                self.env._("The eTIMS connector returned an incomplete answer (missing %s).", ", ".join(missing))
            )
        return result

    def _submit_demo(self, document, payload):
        """Training connector: signs locally, never contacts KRA.

        Produces numbers prefixed with TRAIN so receipts can never be
        mistaken for real fiscal documents.
        """
        company = document.company_id
        if company.ke_etims_environment == "production":
            raise UserError(self.env._("Training mode cannot sign documents in production."))
        receipt_number = int(
            self.env["ir.sequence"].sudo().next_by_code("ke.etims.demo.receipt") or 0
        )
        scu_id = company.ke_etims_scu_id or f"TRAIN{company.id:06d}"
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest().upper()
        signature = "-".join(digest[i : i + 4] for i in range(0, 16, 4))
        return {
            "invoice_number": f"{scu_id}/{receipt_number}",
            "scu_id": scu_id,
            "receipt_number": receipt_number,
            "internal_data": digest[16:42],
            "signature": signature,
            "signed_at": fields.Datetime.now(),
            "qr_data": (
                f"TRAINING-NOT-A-TAX-INVOICE|{payload['seller']['pin']}"
                f"|{payload['seller']['branch_code']}|{signature}"
            ),
            "training": True,
            "raw_response": {"resultCd": "000", "resultMsg": "Training mode"},
        }

    # ------------------------------------------------------------------
    # Connection test (settings button)
    # ------------------------------------------------------------------
    def action_test_connection(self):
        company = self.env.company
        handler = getattr(self, f"_test_connection_{company.ke_etims_connector}", None)
        if handler:
            message = handler(company)
        elif company.ke_etims_connector == "demo":
            message = self.env._("Training mode is active: documents are signed locally, nothing is sent to KRA.")
        else:
            message = self.env._("This connector does not implement a connection test.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": self.env._("eTIMS"), "message": message, "sticky": False},
        }

    # ------------------------------------------------------------------
    # Queue processing
    # ------------------------------------------------------------------
    @api.model
    def _ke_etims_queue_models(self):
        """Models whose records are fiscal documents (extended by ke_pos)."""
        return ["account.move"]

    @api.model
    def _cron_process_queue(self, batch_size=50):
        retry_before = fields.Datetime.now() - timedelta(minutes=RETRY_DELAY_MINUTES)
        for model_name in self._ke_etims_queue_models():
            Model = self.env[model_name]
            last_id = 0
            while True:
                docs = Model.search(
                    [
                        ("id", ">", last_id),
                        ("ke_etims_state", "in", ("to_send", "error")),
                        "|",
                        ("ke_etims_last_attempt_at", "=", False),
                        ("ke_etims_last_attempt_at", "<", retry_before),
                    ],
                    order="id",
                    limit=batch_size,
                )
                if not docs:
                    break
                last_id = docs[-1].id
                docs = docs.filtered(
                    lambda d: d.ke_etims_attempts < (d.company_id.ke_etims_max_attempts or 1)
                )
                docs._ke_etims_send()
                if not self.env["ir.cron"]._commit_progress(len(docs)):
                    return
