from datetime import timedelta

from odoo import api, fields, models


class KeEcommerceRequestLog(models.Model):
    """One line per API call: who called what, the outcome and how long it
    took. Request bodies are only kept for failed calls (troubleshooting) and
    everything is purged after the retention period (Data Protection Act)."""

    _name = "ke.ecommerce.request.log"
    _description = "E-commerce API request log"
    _order = "id desc"

    request_id = fields.Char(index=True, readonly=True)
    channel_id = fields.Many2one("ke.ecommerce.channel", index=True, ondelete="cascade", readonly=True)
    company_id = fields.Many2one(related="channel_id.company_id", store=True)
    api_key_id = fields.Many2one("ke.ecommerce.api.key", ondelete="set null", readonly=True)
    method = fields.Char(readonly=True)
    path = fields.Char(readonly=True)
    status = fields.Integer(readonly=True, index=True)
    error_code = fields.Char(readonly=True)
    duration_ms = fields.Integer(readonly=True)
    ip_address = fields.Char(readonly=True)
    request_body = fields.Text(readonly=True)

    @api.model
    def _cron_purge(self, days=30):
        self.sudo().search([("create_date", "<", fields.Datetime.now() - timedelta(days=days))]).unlink()
