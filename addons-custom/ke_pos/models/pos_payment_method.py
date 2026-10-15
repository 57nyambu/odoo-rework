from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.ke_base.tools import is_valid_mpesa_code, normalize_mpesa_code

ETIMS_PAYMENT_TYPES = [
    ("cash", "Cash"),
    ("credit", "Credit (customer account)"),
    ("card", "Debit / credit card"),
    ("mobile_money", "Mobile money"),
    ("cheque", "Cheque / bank"),
    ("other", "Other"),
]

STK_QUERY_AFTER_SECONDS = 15


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    ke_mpesa_account_id = fields.Many2one(
        "ke.mpesa.account",
        string="M-Pesa account",
        check_company=True,
        help="Till or paybill (with Daraja credentials) customers pay to.",
    )
    ke_mpesa_allow_stk = fields.Boolean(
        string="Send STK push",
        default=True,
        help="Cashier types the customer's phone number; the customer confirms with their PIN.",
    )
    ke_mpesa_allow_code = fields.Boolean(
        string="Verify M-Pesa code",
        default=True,
        help="Customer pays to the till/paybill from their phone; the cashier enters the "
        "M-Pesa confirmation code, which Odoo checks against Safaricom's notification.",
    )
    ke_mpesa_display_number = fields.Char(related="ke_mpesa_account_id.display_number")
    ke_etims_payment_type = fields.Selection(
        ETIMS_PAYMENT_TYPES,
        string="eTIMS payment type",
        compute="_compute_ke_etims_payment_type",
        store=True,
        readonly=False,
        help="Payment type reported to KRA eTIMS for sales paid with this method.",
    )

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [("ke_mpesa", "M-Pesa (Safaricom Daraja)")]

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "ke_mpesa_allow_stk",
            "ke_mpesa_allow_code",
            "ke_mpesa_display_number",
        ]

    @api.depends("journal_id.type", "use_payment_terminal")
    def _compute_ke_etims_payment_type(self):
        for method in self:
            if method.use_payment_terminal == "ke_mpesa":
                method.ke_etims_payment_type = "mobile_money"
            elif method.journal_id.type == "cash":
                method.ke_etims_payment_type = "cash"
            elif not method.journal_id:
                method.ke_etims_payment_type = "credit"
            else:
                method.ke_etims_payment_type = method.ke_etims_payment_type or "card"

    @api.constrains("use_payment_terminal", "ke_mpesa_account_id", "ke_mpesa_allow_stk", "ke_mpesa_allow_code")
    def _check_ke_mpesa(self):
        for method in self:
            if method.use_payment_terminal != "ke_mpesa":
                continue
            if not method.ke_mpesa_account_id:
                raise ValidationError(self.env._("Select the M-Pesa account of %s.", method.name))
            if not (method.ke_mpesa_allow_stk or method.ke_mpesa_allow_code):
                raise ValidationError(
                    self.env._("Enable STK push, code verification or both on %s.", method.name)
                )
            if method.company_id.currency_id.name != "KES":
                raise ValidationError(self.env._("M-Pesa payments are only possible in Kenyan shillings."))

    # ------------------------------------------------------------------
    # RPC used by the POS M-Pesa dialog
    # ------------------------------------------------------------------
    def _ke_mpesa_pos_check_access(self):
        self.ensure_one()
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(self.env._("Only Point of Sale users can take M-Pesa payments."))
        self.check_access("read")
        if self.use_payment_terminal != "ke_mpesa" or not self.ke_mpesa_account_id:
            raise UserError(self.env._("%s is not connected to an M-Pesa account.", self.name))
        return self.ke_mpesa_account_id.sudo()

    def _ke_mpesa_pos_transaction(self, transaction_id):
        account = self._ke_mpesa_pos_check_access()
        tx = self.env["ke.mpesa.transaction"].sudo().browse(int(transaction_id)).exists()
        if not tx or tx.account_id != account:
            raise UserError(self.env._("Unknown M-Pesa transaction."))
        return tx

    def ke_mpesa_pos_stk_push(self, phone, amount, order_uuid, order_reference=None):
        """Send the STK prompt for a POS payment line."""
        account = self._ke_mpesa_pos_check_access()
        if not self.ke_mpesa_allow_stk:
            raise UserError(self.env._("STK push is disabled on %s.", self.name))
        reference = (order_reference or account.company_id.name or "POS").replace(" ", "")[:12]
        tx = account.stk_push(
            phone,
            amount,
            reference=reference,
            description="POS sale",
            extra_vals={"pos_order_uuid": order_uuid, "pos_payment_method_id": self.id},
        )
        return tx._ke_pos_status_vals()

    def ke_mpesa_pos_check_status(self, transaction_id):
        """Polled by the POS while the customer enters their PIN. Falls back
        on Daraja's status query when the callback is late."""
        tx = self._ke_mpesa_pos_transaction(transaction_id)
        if tx.state == "pending" and tx.create_date < fields.Datetime.now() - timedelta(seconds=STK_QUERY_AFTER_SECONDS):
            tx._query_stk_status()
        if tx.state == "confirmed" and not tx.claimed:
            tx._claim(self.env._("POS order %s", tx.pos_order_uuid or ""))
        return tx._ke_pos_status_vals()

    def ke_mpesa_pos_claim_code(self, code, order_uuid):
        """Verify a code from a payment made straight to the till/paybill
        and reserve it for this order."""
        account = self._ke_mpesa_pos_check_access()
        if not self.ke_mpesa_allow_code:
            raise UserError(self.env._("M-Pesa code verification is disabled on %s.", self.name))
        code = normalize_mpesa_code(code)
        if not is_valid_mpesa_code(code):
            raise UserError(
                self.env._("An M-Pesa code has 10 letters and digits, e.g. SJK3ABCD9Z.")
            )
        Tx = self.env["ke.mpesa.transaction"].sudo()
        tx = Tx._find_claimable(code, account)
        if not tx:
            raise UserError(
                self.env._(
                    "No M-Pesa payment %(code)s has reached %(number)s yet. Check the code on the "
                    "customer's SMS, wait a few seconds and try again.",
                    code=code,
                    number=account.display_number,
                )
            )
        if tx.claimed and not (tx.pos_order_uuid == order_uuid and not tx.pos_payment_id):
            raise UserError(
                self.env._(
                    "M-Pesa payment %(code)s has already been used (%(ref)s).",
                    code=code,
                    ref=tx.claim_reference or "",
                )
            )
        if not tx.claimed:
            tx._claim(self.env._("POS order %s", order_uuid))
        tx.write({"pos_order_uuid": order_uuid, "pos_payment_method_id": self.id})
        return tx._ke_pos_status_vals()
