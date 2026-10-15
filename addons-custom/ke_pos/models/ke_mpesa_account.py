from odoo import models


class KeMpesaAccount(models.Model):
    _inherit = "ke.mpesa.account"

    def _ke_new_journal_code(self):
        self.ensure_one()
        Journal = self.env["account.journal"].with_context(active_test=False)
        for index in range(1, 100):
            code = f"MP{index:02d}"
            if not Journal.search_count([("company_id", "=", self.company_id.id), ("code", "=", code)]):
                return code
        return "MPX"

    def action_ke_create_pos_payment_method(self):
        """One click setup: an 'M-Pesa' bank journal + POS payment method
        bound to this till/paybill."""
        self.ensure_one()
        company = self.company_id
        journal = self.env["account.journal"].create(
            {
                "name": self.env._("M-Pesa %s", self.display_number),
                "type": "bank",
                "code": self._ke_new_journal_code(),
                "company_id": company.id,
            }
        )
        chart_template = self.with_context(allowed_company_ids=company.root_id.ids).env["account.chart.template"]
        outstanding = (
            chart_template.ref("account_journal_payment_debit_account_id", raise_if_not_found=False)
            or company.transfer_account_id
        )
        method = self.env["pos.payment.method"].create(
            {
                "name": self.env._("M-Pesa"),
                "journal_id": journal.id,
                "outstanding_account_id": outstanding.id,
                "payment_method_type": "terminal",
                "use_payment_terminal": "ke_mpesa",
                "ke_mpesa_account_id": self.id,
                "company_id": company.id,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "pos.payment.method",
            "res_id": method.id,
            "view_mode": "form",
            "target": "current",
        }
