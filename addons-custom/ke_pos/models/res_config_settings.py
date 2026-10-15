from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # `pos_` prefixed fields are written on the selected pos.config.
    pos_ke_lipa_na_mpesa_text = fields.Char(related="pos_config_id.ke_lipa_na_mpesa_text", readonly=False)
    pos_ke_etims_wait_for_signature = fields.Boolean(
        related="pos_config_id.ke_etims_wait_for_signature", readonly=False
    )
    pos_ke_mpesa_allow_force_done = fields.Boolean(
        related="pos_config_id.ke_mpesa_allow_force_done", readonly=False
    )
