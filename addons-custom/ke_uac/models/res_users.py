from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    ke_pos_config_ids = fields.Many2many(
        "pos.config",
        "ke_res_users_pos_config_rel",
        "user_id",
        "config_id",
        string="Allowed shops",
        help="Cashiers only see and open these points of sale (and their sessions and "
        "orders). Leave empty for no restriction. Ignored for POS administrators.",
    )
