from odoo import fields, models


class MpesaTransaction(models.Model):
    _name = "mpesa.transaction"
    _description = "M-Pesa STK Push transaction log"
    _order = "create_date desc"

    pos_order_id = fields.Many2one("pos.order", string="POS order")
    phone = fields.Char(string="Phone number", required=True)
    amount = fields.Monetary(string="Amount", required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id
    )
    checkout_request_id = fields.Char(string="Daraja checkout request ID", index=True)
    merchant_request_id = fields.Char(string="Daraja merchant request ID")
    mpesa_receipt_number = fields.Char(string="M-Pesa receipt number")
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("confirmed", "Confirmed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
        required=True,
    )
    raw_callback = fields.Text(string="Raw callback payload")
