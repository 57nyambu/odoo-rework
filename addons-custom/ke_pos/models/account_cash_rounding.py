from odoo import api, models


class AccountCashRounding(models.Model):
    _inherit = "account.cash.rounding"

    @api.model
    def _ke_setup_kes_rounding(self, companies=None):
        """The POS requires the 'add a rounding line' strategy, whose profit /
        loss accounts are company dependent: point the shared 'KES 1.00'
        rounding at each company's cash difference accounts."""
        rounding = self.env.ref("ke_pos.cash_rounding_kes_1", raise_if_not_found=False)
        if not rounding:
            return
        companies = companies or self.env["res.company"].search([("chart_template", "!=", False)])
        for company in companies:
            company_rounding = rounding.with_company(company).sudo()
            vals = {}
            if not company_rounding.profit_account_id and company.default_cash_difference_income_account_id:
                vals["profit_account_id"] = company.default_cash_difference_income_account_id.id
            if not company_rounding.loss_account_id and company.default_cash_difference_expense_account_id:
                vals["loss_account_id"] = company.default_cash_difference_expense_account_id.id
            if vals:
                company_rounding.write(vals)


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    def _post_load_data(self, template_code, company, template_data):
        res = super()._post_load_data(template_code, company, template_data)
        self.env["account.cash.rounding"]._ke_setup_kes_rounding(company or self.env.company)
        return res
