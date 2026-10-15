from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    # Each Odoo company (or branch company) maps to one eTIMS registration:
    # the company KRA PIN (`vat`) + a branch ID (bhfId, "00" for head office).
    ke_etims_enabled = fields.Boolean(
        string="eTIMS enabled",
        help="Transmit customer invoices, credit notes and POS receipts to KRA eTIMS.",
    )
    ke_etims_connector = fields.Selection(
        selection=[("demo", "Training mode (no KRA submission)")],
        string="eTIMS connector",
        default="demo",
        help="Implementation used to talk to KRA. Connector modules (OSCU/VSCU "
        "integrations) add their own option here. Training mode signs documents "
        "locally with clearly-marked fake numbers and is refused in production.",
    )
    ke_etims_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        string="eTIMS environment",
        default="sandbox",
    )
    ke_etims_branch_code = fields.Char(
        string="eTIMS branch ID",
        default="00",
        help="KRA branch ID (bhfId). '00' is the head office; branches are '01', '02'...",
    )
    ke_etims_device_serial = fields.Char(
        string="eTIMS device serial",
        help="Serial number of the OSCU/VSCU device registered with KRA (dvcSrlNo).",
    )
    ke_etims_scu_id = fields.Char(
        string="CU / SCU ID",
        help="Control unit identifier returned by KRA at device initialization. "
        "Printed on receipts next to the CU invoice number.",
    )
    ke_etims_max_attempts = fields.Integer(
        string="eTIMS max retries",
        default=12,
        help="After this many failed transmissions the document stays in error "
        "until someone re-sends it manually.",
    )

    @api.constrains("ke_etims_branch_code")
    def _check_ke_etims_branch_code(self):
        for company in self:
            code = company.ke_etims_branch_code
            if code and not (len(code) == 2 and code.isdigit()):
                raise ValidationError(
                    self.env._("The eTIMS branch ID is two digits, e.g. 00 for head office.")
                )

    @api.constrains("ke_etims_enabled", "ke_etims_connector", "ke_etims_environment")
    def _check_ke_etims_training_not_in_production(self):
        for company in self:
            if (
                company.ke_etims_enabled
                and company.ke_etims_connector == "demo"
                and company.ke_etims_environment == "production"
            ):
                raise ValidationError(
                    self.env._(
                        "Training mode only produces fake KRA numbers and cannot be used with "
                        "the production environment. Install and select a real eTIMS connector."
                    )
                )
