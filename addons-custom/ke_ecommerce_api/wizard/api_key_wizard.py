from odoo import fields, models


class KeEcommerceApiKeyWizard(models.TransientModel):
    _name = "ke.ecommerce.api.key.wizard"
    _description = "Generate an e-commerce API key"

    channel_id = fields.Many2one("ke.ecommerce.channel", required=True)
    name = fields.Char(required=True, default="Production")
    scope_ids = fields.Many2many(
        "ke.ecommerce.api.scope",
        string="Scopes",
        default=lambda self: self.env["ke.ecommerce.api.scope"].search([]),
    )
    expires_at = fields.Datetime()
    key = fields.Char(readonly=True, help="Copy it now: it will never be shown again.")

    def action_generate(self):
        self.ensure_one()
        _record, plaintext = self.env["ke.ecommerce.api.key"]._generate(
            self.channel_id, self.name, self.scope_ids, self.expires_at
        )
        self.key = plaintext
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
