from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ke_etims_enabled = fields.Boolean(related="company_id.ke_etims_enabled", readonly=False)
    ke_etims_connector = fields.Selection(related="company_id.ke_etims_connector", readonly=False)
    ke_etims_environment = fields.Selection(
        related="company_id.ke_etims_environment", readonly=False
    )
    ke_etims_branch_code = fields.Char(related="company_id.ke_etims_branch_code", readonly=False)
    ke_etims_device_serial = fields.Char(
        related="company_id.ke_etims_device_serial", readonly=False
    )
    ke_etims_scu_id = fields.Char(related="company_id.ke_etims_scu_id", readonly=False)

    def action_ke_etims_test_connection(self):
        self.ensure_one()
        return self.env["ke.etims.service"].with_company(self.company_id).action_test_connection()

    def action_ke_etims_open_queue(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("eTIMS queue"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("ke_etims_state", "in", ("to_send", "error"))],
        }
