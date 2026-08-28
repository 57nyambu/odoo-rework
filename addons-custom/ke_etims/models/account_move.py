from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    ke_etims_invoice_number = fields.Char(
        string="KRA invoice number", copy=False, readonly=True
    )
    ke_etims_qr_code = fields.Char(string="eTIMS QR code", copy=False, readonly=True)
    ke_etims_submitted_at = fields.Datetime(
        string="eTIMS submitted at", copy=False, readonly=True
    )

    def action_ke_submit_etims(self):
        for move in self:
            if move.state != "posted":
                continue
            result = self.env["ke.etims.service"].sign_and_submit(move)
            move.write(
                {
                    "ke_etims_invoice_number": result.get("kra_invoice_number"),
                    "ke_etims_qr_code": result.get("qr_code"),
                    "ke_etims_submitted_at": fields.Datetime.now(),
                }
            )

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(
            lambda m: m.move_type in ("out_invoice", "out_refund")
        ).action_ke_submit_etims()
        return posted
