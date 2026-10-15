import json
import logging
from datetime import datetime, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.ke_base.tools import mask_ke_phone, normalize_ke_msisdn, normalize_mpesa_code

from .daraja_client import STK_RESULT_MESSAGES, DarajaError

_logger = logging.getLogger(__name__)

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
STK_EXPIRY_MINUTES = 10
RAW_PAYLOAD_RETENTION_DAYS = 90


def _parse_daraja_datetime(value):
    """Daraja sends local (EAT) timestamps as 20240131143015 (int or str)."""
    if not value:
        return False
    try:
        local = NAIROBI_TZ.localize(datetime.strptime(str(value), "%Y%m%d%H%M%S"))
    except ValueError:
        return False
    return local.astimezone(pytz.utc).replace(tzinfo=None)


class KeMpesaTransaction(models.Model):
    """Every M-Pesa payment Odoo knows about.

    * ``stk``: requested by Odoo (Lipa na M-Pesa Online prompt), confirmed
      by Safaricom's callback or a status query.
    * ``c2b``: paid by the customer straight to the till/paybill from their
      phone; reported by Safaricom's C2B confirmation callback and later
      *claimed* by a sale (POS order, online order) using the M-Pesa code.

    Users only get read access: state changes happen exclusively in server
    code (callbacks, status queries, claims) so nobody can mark a payment
    as received from the UI or through RPC.
    """

    _name = "ke.mpesa.transaction"
    _description = "M-Pesa transaction"
    _order = "create_date desc, id desc"
    _rec_name = "name"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    account_id = fields.Many2one("ke.mpesa.account", required=True, index=True, ondelete="restrict", check_company=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    kind = fields.Selection([("stk", "STK push"), ("c2b", "Till / Paybill (C2B)")], required=True, default="stk")
    state = fields.Selection(
        [
            ("pending", "Waiting for customer"),
            ("confirmed", "Confirmed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
            ("expired", "Expired"),
        ],
        required=True,
        default="pending",
        index=True,
    )
    phone = fields.Char(string="Phone number", help="Normalized MSISDN (2547XXXXXXXX). C2B callbacks may only give a masked number.")
    phone_masked = fields.Char(compute="_compute_phone_masked", string="Phone")
    payer_name = fields.Char()
    amount = fields.Monetary(required=True)
    reference = fields.Char(help="Account reference / order reference the payment was made for.", index=True)
    receipt_number = fields.Char(string="M-Pesa code", index=True, copy=False)
    checkout_request_id = fields.Char(index=True, copy=False)
    merchant_request_id = fields.Char(copy=False)
    result_code = fields.Char()
    result_desc = fields.Char(string="Result")
    transaction_date = fields.Datetime(help="Time Safaricom completed the payment.")
    last_query_at = fields.Datetime(copy=False)
    claimed = fields.Boolean(index=True, copy=False, help="Already used to pay a sale.")
    claimed_at = fields.Datetime(copy=False)
    claimed_by_id = fields.Many2one("res.users", copy=False)
    claim_reference = fields.Char(copy=False, help="Document that used this payment.")
    raw_callback = fields.Text(groups="ke_mpesa.group_mpesa_manager", copy=False)

    _receipt_uniq = models.Constraint(
        "unique(company_id, receipt_number)", "This M-Pesa code is already recorded."
    )
    _checkout_uniq = models.Constraint(
        "unique(checkout_request_id)", "Duplicate STK checkout request ID."
    )

    @api.depends("receipt_number", "checkout_request_id", "kind")
    def _compute_name(self):
        for tx in self:
            tx.name = tx.receipt_number or tx.checkout_request_id or self.env._("Draft M-Pesa payment")

    @api.depends("phone")
    def _compute_phone_masked(self):
        for tx in self:
            tx.phone_masked = mask_ke_phone(tx.phone) if tx.phone else False

    # ------------------------------------------------------------------
    # Hooks for other modules
    # ------------------------------------------------------------------
    def _ke_on_confirmed(self):
        """Called once when a transaction becomes confirmed (override)."""

    def _ke_on_failed(self):
        """Called once when a transaction fails / expires (override)."""

    def _set_state(self, vals):
        """Write a final state exactly once and fire hooks."""
        previous = {tx.id: tx.state for tx in self}
        self.write(vals)
        confirmed = self.filtered(lambda tx: tx.state == "confirmed" and previous[tx.id] != "confirmed")
        failed = self.filtered(
            lambda tx: tx.state in ("failed", "expired", "cancelled") and previous[tx.id] == "pending"
        )
        if confirmed:
            confirmed._ke_on_confirmed()
        if failed:
            failed._ke_on_failed()

    # ------------------------------------------------------------------
    # Safaricom callbacks
    # ------------------------------------------------------------------
    @api.model
    def _process_stk_callback(self, account, payload):
        """Handle a Lipa na M-Pesa Online result. Idempotent: Safaricom
        retries callbacks, so an already settled transaction is ignored."""
        body = (payload or {}).get("Body", {}).get("stkCallback", {})
        checkout_request_id = body.get("CheckoutRequestID")
        if not checkout_request_id:
            return False
        result_code = str(body.get("ResultCode"))
        items = {
            item.get("Name"): item.get("Value")
            for item in body.get("CallbackMetadata", {}).get("Item", [])
            if isinstance(item, dict)
        }
        tx = self.sudo().search(
            [("checkout_request_id", "=", checkout_request_id), ("account_id", "=", account.id)], limit=1
        )
        if not tx:
            if result_code != "0":
                return False
            # Money was received but the request is unknown (e.g. Odoo's
            # transaction was rolled back after Daraja accepted the push).
            # Record it so the payment is never lost; it can be claimed later.
            _logger.warning("Orphan STK callback %s recorded as unclaimed payment", checkout_request_id)
            tx = self.sudo().create(
                {
                    "account_id": account.id,
                    "kind": "stk",
                    "state": "pending",
                    "checkout_request_id": checkout_request_id,
                    "merchant_request_id": body.get("MerchantRequestID"),
                    "phone": normalize_ke_msisdn(str(items.get("PhoneNumber") or "")),
                    "amount": float(items.get("Amount") or 0.0),
                }
            )
        receipt = normalize_mpesa_code(items.get("MpesaReceiptNumber"))
        if tx.state == "confirmed":
            # Duplicate callback, or confirmed earlier by a status query
            # (which carries no receipt number): only backfill the code.
            if result_code == "0" and receipt and not tx.receipt_number:
                tx.write({"receipt_number": receipt, "raw_callback": json.dumps(payload)})
            return tx
        if tx.state != "pending" and result_code != "0":
            return tx
        # A success always wins, even after the request was marked expired
        # or failed: the customer's money has been received.
        vals = {
            "result_code": result_code,
            "result_desc": body.get("ResultDesc"),
            "raw_callback": json.dumps(payload),
        }
        if result_code == "0":
            vals.update(
                {
                    "state": "confirmed",
                    "receipt_number": receipt,
                    "transaction_date": _parse_daraja_datetime(items.get("TransactionDate")) or fields.Datetime.now(),
                }
            )
            if items.get("Amount") is not None:
                vals["amount"] = float(items["Amount"])
        else:
            vals["state"] = "cancelled" if result_code == "1032" else "failed"
        tx._set_state(vals)
        return tx

    @api.model
    def _c2b_validate(self, account, payload):
        """Return (accepted: bool, result_code: str). Override to reject
        payments (e.g. unknown account numbers on a paybill)."""
        try:
            amount = float(payload.get("TransAmount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return False, "C2B00013"  # invalid amount
        return True, "0"

    @api.model
    def _process_c2b_confirmation(self, account, payload):
        code = normalize_mpesa_code(payload.get("TransID"))
        if not code:
            return False
        existing = self.sudo().search(
            [("company_id", "=", account.company_id.id), ("receipt_number", "=", code)], limit=1
        )
        if existing:
            return existing
        names = " ".join(filter(None, (payload.get(k) for k in ("FirstName", "MiddleName", "LastName"))))
        tx = self.sudo().create(
            {
                "account_id": account.id,
                "kind": "c2b",
                "state": "pending",
                "receipt_number": code,
                "amount": float(payload.get("TransAmount") or 0.0),
                # Safaricom now masks / hashes the MSISDN on C2B callbacks.
                "phone": normalize_ke_msisdn(payload.get("MSISDN")) or payload.get("MSISDN"),
                "payer_name": names or False,
                "reference": payload.get("BillRefNumber") or False,
                "raw_callback": json.dumps(payload),
            }
        )
        tx._set_state(
            {
                "state": "confirmed",
                "result_code": "0",
                "result_desc": payload.get("TransactionType") or "C2B payment",
                "transaction_date": _parse_daraja_datetime(payload.get("TransTime")) or fields.Datetime.now(),
            }
        )
        return tx

    # ------------------------------------------------------------------
    # Status query (callback never arrived)
    # ------------------------------------------------------------------
    def action_query_status(self):
        for tx in self.sudo().filtered(lambda t: t.kind == "stk" and t.state == "pending" and t.checkout_request_id):
            tx._query_stk_status()
        return True

    def _query_stk_status(self, min_interval=8):
        self.ensure_one()
        now = fields.Datetime.now()
        if self.last_query_at and self.last_query_at > now - timedelta(seconds=min_interval):
            return self
        self.last_query_at = now
        account = self.account_id.sudo()
        try:
            data = account._daraja().stk_query(
                shortcode=account.business_shortcode,
                passkey=account.passkey,
                checkout_request_id=self.checkout_request_id,
            )
        except DarajaError as error:
            # "The transaction is being processed" comes back as an error.
            _logger.info("STK query for %s: %s", self.checkout_request_id, error.message)
            return self
        result_code = str(data.get("ResultCode", ""))
        if result_code == "0":
            # The query answer has no receipt number; the callback (if it
            # ever arrives) fills it in. The payment is nonetheless final.
            self._set_state(
                {
                    "state": "confirmed",
                    "result_code": result_code,
                    "result_desc": data.get("ResultDesc"),
                    "transaction_date": now,
                }
            )
        elif result_code:
            self._set_state(
                {
                    "state": "cancelled" if result_code == "1032" else "failed",
                    "result_code": result_code,
                    "result_desc": data.get("ResultDesc"),
                }
            )
        return self

    # ------------------------------------------------------------------
    # Claiming (match a payment to a sale)
    # ------------------------------------------------------------------
    @api.model
    def _find_claimable(self, code, accounts):
        """Confirmed, not yet claimed payment with this M-Pesa code on one of
        ``accounts``."""
        code = normalize_mpesa_code(code)
        if not code:
            return self.browse()
        return self.sudo().search(
            [
                ("receipt_number", "=", code),
                ("account_id", "in", accounts.ids),
                ("state", "=", "confirmed"),
            ],
            limit=1,
        )

    def _claim(self, claim_reference):
        """Atomically mark this payment as used. Raises if it was already
        claimed (e.g. the same M-Pesa code typed at two tills)."""
        self.ensure_one()
        # pending ORM writes (e.g. a release in the same transaction) must
        # reach the database before the atomic UPDATE below
        self.flush_recordset()
        self.env.cr.execute(
            "UPDATE ke_mpesa_transaction SET claimed = TRUE, claimed_at = NOW() AT TIME ZONE 'UTC', "
            "claimed_by_id = %s, claim_reference = %s WHERE id = %s AND claimed IS NOT TRUE RETURNING id",
            (self.env.uid, claim_reference, self.id),
        )
        if not self.env.cr.fetchone():
            raise UserError(
                self.env._(
                    "M-Pesa payment %(code)s has already been used for %(ref)s.",
                    code=self.receipt_number or self.checkout_request_id,
                    ref=self.sudo().claim_reference or self.env._("another sale"),
                )
            )
        self.invalidate_recordset(["claimed", "claimed_at", "claimed_by_id", "claim_reference"])
        return self

    def _ke_result_message(self):
        self.ensure_one()
        return STK_RESULT_MESSAGES.get(self.result_code or "") or self.result_desc or ""

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    @api.model
    def _cron_settle_pending(self):
        """Query STK requests whose callback never came, then expire them."""
        now = fields.Datetime.now()
        stale = self.sudo().search(
            [
                ("kind", "=", "stk"),
                ("state", "=", "pending"),
                ("create_date", "<", now - timedelta(minutes=1)),
            ],
            limit=200,
        )
        for tx in stale:
            tx._query_stk_status(min_interval=30)
            if tx.state == "pending" and tx.create_date < now - timedelta(minutes=STK_EXPIRY_MINUTES):
                tx._set_state({"state": "expired", "result_desc": self.env._("No answer from M-Pesa.")})

    @api.model
    def _cron_purge_raw_payloads(self):
        """Kenya Data Protection Act, 2019 (data minimisation): the raw
        Safaricom payloads duplicate personal data already stored in
        structured fields; drop them after the retention period."""
        days = int(
            self.env["ir.config_parameter"].sudo().get_param(
                "ke_mpesa.raw_payload_retention_days", RAW_PAYLOAD_RETENTION_DAYS
            )
        )
        old = self.sudo().search(
            [("raw_callback", "!=", False), ("create_date", "<", fields.Datetime.now() - timedelta(days=days))]
        )
        old.write({"raw_callback": False})
