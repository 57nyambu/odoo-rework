from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    ke_lipa_na_mpesa_text = fields.Char(
        string="Lipa na M-Pesa line",
        compute="_compute_ke_lipa_na_mpesa_text",
        store=True,
        readonly=False,
        help="Printed on receipts so customers know where to pay, e.g. "
        "'Lipa na M-Pesa - Buy Goods Till No. 5123456'. Leave empty to hide.",
    )
    ke_etims_wait_for_signature = fields.Boolean(
        string="Wait for eTIMS before printing",
        default=True,
        help="After a sale is saved, sign it with KRA eTIMS before printing so the "
        "receipt carries the CU invoice number and QR code. If KRA does not answer, "
        "the receipt prints with 'eTIMS pending' and the sale is transmitted later.",
    )
    ke_mpesa_allow_force_done = fields.Boolean(
        string="Allow forcing M-Pesa payments",
        help="Let cashiers mark an M-Pesa payment line as paid without Safaricom's "
        "confirmation. Leave off: use 'Verify M-Pesa code' instead.",
    )

    @api.depends("payment_method_ids.ke_mpesa_account_id")
    def _compute_ke_lipa_na_mpesa_text(self):
        for config in self:
            text = config.ke_lipa_na_mpesa_text
            account = config.payment_method_ids.ke_mpesa_account_id[:1]
            if not text and account:
                if account.shortcode_type == "till":
                    text = self.env._("Lipa na M-Pesa - Buy Goods Till No. %s", account.till_number)
                else:
                    text = self.env._("Lipa na M-Pesa - Paybill %s", account.business_shortcode)
            config.ke_lipa_na_mpesa_text = text
