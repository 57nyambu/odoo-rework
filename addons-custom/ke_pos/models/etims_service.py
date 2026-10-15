from odoo import api, models


class KeEtimsService(models.AbstractModel):
    _inherit = "ke.etims.service"

    @api.model
    def _ke_etims_queue_models(self):
        return super()._ke_etims_queue_models() + ["pos.order"]
