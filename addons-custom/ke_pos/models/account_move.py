from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _ke_etims_is_applicable(self):
        # A POS receipt already transmitted to eTIMS and invoiced afterwards
        # must not be fiscalized a second time.
        if self.pos_order_ids.filtered(lambda o: o.ke_etims_state == "sent"):
            return False
        return super()._ke_etims_is_applicable()

    def _ke_etims_apply_result(self, result):
        res = super()._ke_etims_apply_result(result)
        self.sudo().pos_order_ids._ke_etims_copy_from_invoice()
        return res
