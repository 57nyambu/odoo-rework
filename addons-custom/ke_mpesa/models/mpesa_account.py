import logging
import math
import secrets
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.ke_base.tools import normalize_ke_msisdn

from .daraja_client import DarajaClient, DarajaError

_logger = logging.getLogger(__name__)


def _new_callback_token():
    # Hex only: Daraja rejects callback URLs containing words such as
    # "exe", "cmd", "sql" or "query", which a base64 token could contain.
    return secrets.token_hex(24)


class KeMpesaAccount(models.Model):
    """An M-Pesa business number (Paybill or Buy Goods till) and its Daraja
    credentials. One company can own several (one per branch till...).
    Used by the POS, the e-commerce API and anything else that needs to
    request or receive M-Pesa payments."""

    _name = "ke.mpesa.account"
    _description = "M-Pesa business account (Daraja)"
    _order = "company_id, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, help="e.g. 'Moi Avenue till'")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")], required=True, default="sandbox"
    )
    shortcode_type = fields.Selection(
        [("paybill", "Paybill"), ("till", "Buy Goods (Till)")],
        string="Number type",
        required=True,
        default="till",
    )
    business_shortcode = fields.Char(
        string="Business shortcode",
        required=True,
        help="Paybill number, or for a Buy Goods till the store/head-office "
        "number that owns the till (used to sign STK requests).",
    )
    till_number = fields.Char(
        help="Buy Goods till number customers pay to (PartyB). Leave empty for Paybill."
    )
    display_number = fields.Char(
        compute="_compute_display_number",
        string="Customer-facing number",
        help="Number printed on receipts ('Lipa na M-Pesa').",
    )
    consumer_key = fields.Char(groups="ke_mpesa.group_mpesa_manager", copy=False)
    consumer_secret = fields.Char(groups="ke_mpesa.group_mpesa_manager", copy=False)
    passkey = fields.Char(
        string="Lipa na M-Pesa Online passkey", groups="ke_mpesa.group_mpesa_manager", copy=False
    )
    callback_token = fields.Char(
        default=lambda self: _new_callback_token(),
        copy=False,
        required=True,
        groups="ke_mpesa.group_mpesa_manager",
        help="Secret part of the callback URLs given to Safaricom. Daraja does not "
        "sign callbacks, so an unguessable URL is what proves a callback is genuine.",
    )
    callback_base_url = fields.Char(
        help="Public HTTPS address of this Odoo (tenant) as seen by Safaricom. "
        "Defaults to the web.base.url system parameter.",
    )
    stk_callback_url = fields.Char(compute="_compute_callback_urls", groups="ke_mpesa.group_mpesa_manager")
    c2b_confirmation_url = fields.Char(compute="_compute_callback_urls", groups="ke_mpesa.group_mpesa_manager")
    c2b_validation_url = fields.Char(compute="_compute_callback_urls", groups="ke_mpesa.group_mpesa_manager")
    c2b_response_type = fields.Selection(
        [("Completed", "Complete payments"), ("Cancelled", "Cancel payments")],
        string="If Odoo is unreachable",
        default="Completed",
        help="What Safaricom does with a till/paybill payment when the validation URL "
        "does not answer.",
    )
    c2b_registered_at = fields.Datetime(string="C2B URLs registered", readonly=True, copy=False)
    access_token = fields.Char(groups="base.group_system", copy=False)
    access_token_expires_at = fields.Datetime(groups="base.group_system", copy=False)
    transaction_ids = fields.One2many("ke.mpesa.transaction", "account_id")
    transaction_count = fields.Integer(compute="_compute_transaction_count")

    _callback_token_uniq = models.Constraint(
        "unique(callback_token)", "The callback token must be unique."
    )

    @api.depends("shortcode_type", "business_shortcode", "till_number")
    def _compute_display_number(self):
        for account in self:
            account.display_number = (
                account.till_number if account.shortcode_type == "till" else account.business_shortcode
            )

    @api.depends("callback_token", "callback_base_url")
    def _compute_callback_urls(self):
        for account in self:
            base = account._callback_base()
            token = account.sudo().callback_token
            account.stk_callback_url = f"{base}/ke/lipa/stk/{token}"
            account.c2b_confirmation_url = f"{base}/ke/lipa/c2b/confirm/{token}"
            account.c2b_validation_url = f"{base}/ke/lipa/c2b/validate/{token}"

    def _compute_transaction_count(self):
        counts = dict(
            self.env["ke.mpesa.transaction"]._read_group(
                [("account_id", "in", self.ids)], ["account_id"], ["__count"]
            )
        )
        for account in self:
            account.transaction_count = counts.get(account, 0)

    @api.constrains("shortcode_type", "till_number", "business_shortcode")
    def _check_numbers(self):
        for account in self:
            if not (account.business_shortcode or "").isdigit():
                raise ValidationError(self.env._("The business shortcode must contain digits only."))
            if account.shortcode_type == "till" and not (account.till_number or "").isdigit():
                raise ValidationError(self.env._("Enter the Buy Goods till number (digits only)."))

    def _callback_base(self):
        self.ensure_one()
        base = self.callback_base_url or self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return base.rstrip("/")

    # ------------------------------------------------------------------
    # Daraja client
    # ------------------------------------------------------------------
    def _daraja(self):
        self.ensure_one()
        account = self.sudo()

        def token_getter():
            if account.access_token and account.access_token_expires_at and (
                account.access_token_expires_at > fields.Datetime.now() + timedelta(seconds=60)
            ):
                return account.access_token
            return None

        def token_setter(token, expires_in):
            account.write(
                {
                    "access_token": token,
                    "access_token_expires_at": fields.Datetime.now() + timedelta(seconds=expires_in),
                }
            )

        return DarajaClient(
            account.environment,
            account.consumer_key,
            account.consumer_secret,
            token_getter=token_getter,
            token_setter=token_setter,
        )

    def _stk_transaction_type(self):
        self.ensure_one()
        return "CustomerBuyGoodsOnline" if self.shortcode_type == "till" else "CustomerPayBillOnline"

    def _stk_party_b(self):
        self.ensure_one()
        return self.till_number if self.shortcode_type == "till" else self.business_shortcode

    # ------------------------------------------------------------------
    # Business API
    # ------------------------------------------------------------------
    def stk_push(self, phone, amount, reference, description=None, extra_vals=None):
        """Send a Lipa na M-Pesa Online (STK) prompt to ``phone``.

        :param phone: any Kenyan mobile format
        :param amount: amount in KES; M-Pesa only accepts whole shillings
        :param reference: account reference shown to the customer (12 chars max)
        :param extra_vals: values written on the transaction (links to POS
            order, sale order...)
        :return: the pending ``ke.mpesa.transaction``
        :raise UserError: invalid input or Daraja refused the request
        """
        self.ensure_one()
        if not self.active:
            raise UserError(self.env._("The M-Pesa account %s is archived.", self.name))
        msisdn = normalize_ke_msisdn(phone)
        if not msisdn:
            raise UserError(
                self.env._("%s is not a valid Kenyan mobile number (use 07XX XXX XXX).", phone or "")
            )
        if amount is None or amount < 1:
            raise UserError(self.env._("M-Pesa payments must be at least KES 1."))
        if not math.isclose(amount, round(amount), abs_tol=0.001):
            raise UserError(
                self.env._(
                    "M-Pesa only accepts whole shillings (requested KES %s). Enable cash rounding "
                    "to KES 1 or adjust the amount.",
                    amount,
                )
            )
        account = self.sudo()
        transaction = self.env["ke.mpesa.transaction"].sudo().create(
            {
                "account_id": account.id,
                "kind": "stk",
                "phone": msisdn,
                "amount": round(amount),
                "reference": reference,
                "state": "pending",
                **(extra_vals or {}),
            }
        )
        try:
            response = account._daraja().stk_push(
                shortcode=account.business_shortcode,
                passkey=account.passkey,
                transaction_type=account._stk_transaction_type(),
                party_b=account._stk_party_b(),
                phone=msisdn,
                amount=round(amount),
                callback_url=account.stk_callback_url,
                account_reference=reference,
                description=description or account.company_id.name,
            )
        except DarajaError as error:
            _logger.warning("STK push failed for %s: %s", account.name, error.message)
            raise UserError(self.env._("M-Pesa request failed: %s", error.message)) from error
        transaction.write(
            {
                "checkout_request_id": response.get("CheckoutRequestID"),
                "merchant_request_id": response.get("MerchantRequestID"),
                "result_desc": response.get("CustomerMessage") or response.get("ResponseDescription"),
            }
        )
        return transaction

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def _notify(self, message, notif_type="success"):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": self.env._("M-Pesa"), "message": message, "type": notif_type},
        }

    def action_test_credentials(self):
        self.ensure_one()
        try:
            self._daraja().get_access_token(force=True)
        except DarajaError as error:
            return self._notify(self.env._("Daraja rejected the credentials: %s", error.message), "danger")
        return self._notify(self.env._("Daraja credentials are valid."))

    def action_register_c2b_urls(self):
        """Tell Safaricom where to send till/paybill payments made directly
        from the customer's phone (needed for 'verify M-Pesa code')."""
        self.ensure_one()
        account = self.sudo()
        try:
            account._daraja().register_c2b_urls(
                shortcode=account.business_shortcode,
                confirmation_url=account.c2b_confirmation_url,
                validation_url=account.c2b_validation_url,
                response_type=account.c2b_response_type,
            )
        except DarajaError as error:
            raise UserError(self.env._("C2B URL registration failed: %s", error.message)) from error
        account.c2b_registered_at = fields.Datetime.now()
        return self._notify(self.env._("C2B confirmation and validation URLs registered with Safaricom."))

    def action_regenerate_callback_token(self):
        for account in self.sudo():
            account.callback_token = _new_callback_token()
            account.c2b_registered_at = False
        return self._notify(
            self.env._("New callback URLs generated. Register the C2B URLs again."), "warning"
        )

    def action_view_transactions(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("ke_mpesa.action_ke_mpesa_transaction")
        action["domain"] = [("account_id", "=", self.id)]
        action["context"] = {"default_account_id": self.id}
        return action
