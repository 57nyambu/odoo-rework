from odoo import api, fields, models
from odoo.tools import float_compare

# KRA eTIMS tax type codes (taxTyCd). Every item on a fiscal document must
# carry one of them.
ETIMS_TAX_TYPES = [
    ("A", "A - Exempt"),
    ("B", "B - VAT 16%"),
    ("C", "C - Zero rated (0%)"),
    ("D", "D - Non-VAT"),
    ("E", "E - VAT 8%"),
]
ETIMS_TAX_RATES = {"A": 0.0, "B": 16.0, "C": 0.0, "D": 0.0, "E": 8.0}


class AccountTax(models.Model):
    _inherit = "account.tax"

    ke_etims_tax_type = fields.Selection(
        ETIMS_TAX_TYPES,
        string="eTIMS tax type",
        compute="_compute_ke_etims_tax_type",
        store=True,
        readonly=False,
        precompute=True,
        help="KRA eTIMS tax category reported for lines using this tax. Set "
        "automatically for VAT sales taxes (16% → B, 8% → E, 0% → C or A when the "
        "tax is named 'exempt'); leave empty for levies and withholding taxes.",
    )

    @api.depends("amount", "amount_type", "type_tax_use", "name", "invoice_label")
    def _compute_ke_etims_tax_type(self):
        for tax in self:
            if tax.type_tax_use != "sale" or tax.amount_type != "percent" or tax.amount < 0:
                tax.ke_etims_tax_type = False
            elif float_compare(tax.amount, 16.0, precision_digits=4) == 0:
                tax.ke_etims_tax_type = "B"
            elif float_compare(tax.amount, 8.0, precision_digits=4) == 0:
                tax.ke_etims_tax_type = "E"
            elif float_compare(tax.amount, 0.0, precision_digits=4) == 0:
                label = f"{tax.name or ''} {tax.invoice_label or ''}".lower()
                tax.ke_etims_tax_type = "A" if "exempt" in label else "C"
            else:
                # Catering levy, tourism levy... are not VAT categories.
                tax.ke_etims_tax_type = False

    @api.model
    def _ke_etims_tax_type_for(self, taxes):
        """eTIMS tax type of a line given its taxes: the first VAT tax wins;
        a line without any VAT tax is reported as D (non-VAT)."""
        flat = taxes.flatten_taxes_hierarchy() if taxes else taxes
        for tax in flat:
            if tax.ke_etims_tax_type:
                return tax.ke_etims_tax_type
        return "D"
