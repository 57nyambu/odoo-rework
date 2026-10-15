import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class PosPayment(models.Model):
    _inherit = "pos.payment"

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        payments.filtered(
            lambda p: p.payment_method_id.use_payment_terminal == "ke_mpesa"
        )._ke_link_mpesa_transactions()
        return payments

    def _ke_link_mpesa_transactions(self):
        """Attach each M-Pesa POS payment to the Safaricom transaction it
        was paid with (M-Pesa code in `transaction_id`, STK checkout ID in
        `payment_ref_no`)."""
        Tx = self.env["ke.mpesa.transaction"].sudo()
        for payment in self:
            account = payment.payment_method_id.ke_mpesa_account_id
            if not account or not (payment.transaction_id or payment.payment_ref_no):
                continue
            domain = [("account_id", "=", account.id), ("pos_payment_id", "=", False)]
            tx = Tx.browse()
            if payment.payment_ref_no:
                tx = Tx.search(domain + [("checkout_request_id", "=", payment.payment_ref_no)], limit=1)
            if not tx and payment.transaction_id:
                tx = Tx.search(domain + [("receipt_number", "=", payment.transaction_id)], limit=1)
            if not tx:
                _logger.warning(
                    "POS payment %s (%s) has no matching M-Pesa transaction", payment.id, payment.transaction_id
                )
                continue
            vals = {
                "pos_payment_id": payment.id,
                "pos_order_id": payment.pos_order_id.id,
                "pos_order_uuid": payment.pos_order_id.uuid,
            }
            if not tx.claimed:
                vals.update({"claimed": True, "claim_reference": payment.pos_order_id.name})
            else:
                vals["claim_reference"] = payment.pos_order_id.name
            tx.write(vals)
