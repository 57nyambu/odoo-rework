from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    ke_is_mpesa = fields.Boolean(string="Is M-Pesa STK Push")
    ke_mpesa_shortcode = fields.Char(string="Business shortcode")
    ke_mpesa_passkey = fields.Char(string="Daraja passkey")
    ke_mpesa_consumer_key = fields.Char(string="Daraja consumer key")
    ke_mpesa_consumer_secret = fields.Char(string="Daraja consumer secret")
    ke_mpesa_environment = fields.Selection(
        [("sandbox", "Sandbox"), ("production", "Production")],
        default="sandbox",
    )
