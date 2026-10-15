from odoo import fields, models
from odoo.exceptions import UserError


class KeMpesaTransaction(models.Model):
    _inherit = "ke.mpesa.transaction"

    pos_order_uuid = fields.Char(string="POS order UUID", index=True, copy=False)
    pos_payment_method_id = fields.Many2one("pos.payment.method", copy=False)
    pos_order_id = fields.Many2one("pos.order", string="POS order", index="btree_not_null", copy=False)
    pos_payment_id = fields.Many2one("pos.payment", string="POS payment", copy=False)

    def _ke_pos_status_vals(self):
        self.ensure_one()
        return {
            "id": self.id,
            "state": self.state,
            "receipt_number": self.receipt_number or False,
            "checkout_request_id": self.checkout_request_id or False,
            "amount": self.amount,
            "phone_masked": self.phone_masked or False,
            "payer_name": self.payer_name or False,
            "message": self._ke_result_message(),
        }

    def _ke_release_claim(self):
        self.filtered(lambda tx: tx.claimed and not tx.pos_payment_id).sudo().write(
            {
                "claimed": False,
                "claimed_at": False,
                "claimed_by_id": False,
                "claim_reference": False,
                "pos_order_uuid": False,
            }
        )

    def action_release_claim(self):
        """Manager action: free a payment reserved by a POS order that was
        never completed so it can be used for another sale."""
        if not self.env.user.has_group("ke_mpesa.group_mpesa_manager"):
            raise UserError(self.env._("Only M-Pesa administrators can release payments."))
        if self.filtered("pos_payment_id"):
            raise UserError(self.env._("This payment is already part of a completed POS order."))
        self._ke_release_claim()
        return True
