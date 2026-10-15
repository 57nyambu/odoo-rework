from odoo import api, fields, models
from odoo.fields import Domain

PRODUCT_TRACKED_FIELDS = {
    "name",
    "list_price",
    "default_code",
    "barcode",
    "active",
    "sale_ok",
    "taxes_id",
    "categ_id",
    "description_sale",
    "image_1920",
    "ke_ecommerce_channel_ids",
    "weight",
}


class AccountPayment(models.Model):
    _inherit = "account.payment"

    ke_channel_id = fields.Many2one("ke.ecommerce.channel", string="Online channel", index="btree_not_null", copy=False)
    ke_sale_order_id = fields.Many2one("sale.order", string="Online order", index="btree_not_null", copy=False)
    ke_external_ref = fields.Char(string="Online payment reference", copy=False, index=True)
    ke_payment_method = fields.Selection(
        [
            ("mpesa", "M-Pesa"),
            ("airtel_money", "Airtel Money"),
            ("card", "Card"),
            ("bank_transfer", "Bank transfer"),
            ("cash_on_delivery", "Cash on delivery"),
            ("other", "Other"),
        ],
        string="Online payment method",
        copy=False,
    )
    ke_payer_phone = fields.Char(string="Payer phone", copy=False)

    _ke_external_ref_uniq = models.Constraint(
        "unique(ke_channel_id, ke_external_ref)", "This online payment was already recorded."
    )


class AccountMove(models.Model):
    _inherit = "account.move"

    def _ke_channel_orders(self):
        return self.line_ids.sale_line_ids.order_id.filtered("ke_channel_id")

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted.filtered(lambda m: m.move_type in ("out_invoice", "out_refund")):
            orders = move._ke_channel_orders()
            if orders:
                orders._ke_reconcile_online_payments()
                orders._ke_emit("order.invoiced")
        return posted

    def _ke_etims_apply_result(self, result):
        res = super()._ke_etims_apply_result(result)
        orders = self._ke_channel_orders()
        Event = self.env["ke.ecommerce.webhook.event"]
        for order in orders:
            channel = order.ke_channel_id._api_env()
            Event._enqueue(
                order.ke_channel_id,
                "invoice.etims_signed",
                {"order": channel._api_order_ref(order), "invoice": channel._api_invoice_payload(self.with_env(channel.env))},
                self,
            )
        return res


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self):
        res = super()._action_done()
        orders = self.sale_id.filtered("ke_channel_id")
        if orders:
            orders._ke_emit("order.fulfilled")
            orders._ke_try_auto_invoice()
            orders._ke_reconcile_online_payments()
        return res


class ResPartner(models.Model):
    _inherit = "res.partner"

    ke_ecommerce_channel_id = fields.Many2one("ke.ecommerce.channel", string="Online channel", copy=False)
    ke_ecommerce_ref = fields.Char(string="Online customer ID", copy=False, index=True)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ke_ecommerce_channel_ids = fields.Many2many(
        "ke.ecommerce.channel",
        string="Online channels",
        help="Online shops this product is published on.",
    )

    def write(self, vals):
        channels_before = (
            {template.id: template.ke_ecommerce_channel_ids for template in self}
            if "ke_ecommerce_channel_ids" in vals
            else {}
        )
        res = super().write(vals)
        if PRODUCT_TRACKED_FIELDS.intersection(vals):
            self.product_variant_ids._ke_notify_product_update()
        for template in self.filtered(lambda t: t.id in channels_before):
            removed = channels_before[template.id] - template.ke_ecommerce_channel_ids
            for channel in removed.filtered(lambda c: not c.publish_all_products):
                template.product_variant_ids._ke_notify_unpublished(channel)
        return res


class ProductProduct(models.Model):
    _inherit = "product.product"

    def write(self, vals):
        res = super().write(vals)
        if PRODUCT_TRACKED_FIELDS.intersection(vals) or "lst_price" in vals:
            self._ke_notify_product_update()
        return res

    def _ke_notify_product_update(self):
        if not self:
            return
        channels = self.env["ke.ecommerce.channel"].sudo().search([("active", "=", True), ("webhook_url", "!=", False)])
        Event = self.env["ke.ecommerce.webhook.event"]
        for channel in channels.filtered(lambda c: c._subscribed("product.updated")):
            api_channel = channel._api_env()
            published = api_channel.env["product.product"].with_context(active_test=False).search(
                api_channel._api_product_domain() & Domain("id", "in", self.ids)
            )
            for product in published:
                Event._enqueue(channel, "product.updated", api_channel._api_product_payload(product), product, debounce=True)

    def _ke_notify_unpublished(self, channel):
        """The product was removed from ``channel``: tell the shop to hide it."""
        if not channel.webhook_url or not channel._subscribed("product.updated"):
            return
        Event = self.env["ke.ecommerce.webhook.event"]
        for product in self:
            Event._enqueue(
                channel,
                "product.updated",
                {"id": product.id, "sku": product.default_code or None, "published": False, "active": False},
                product,
                debounce=True,
            )


class KeMpesaTransaction(models.Model):
    _inherit = "ke.mpesa.transaction"

    ke_sale_order_id = fields.Many2one("sale.order", string="Online order", index="btree_not_null", copy=False)
    ke_account_payment_id = fields.Many2one("account.payment", string="Online payment", copy=False)

    def _ke_on_confirmed(self):
        res = super()._ke_on_confirmed()
        for tx in self.filtered(lambda t: t.ke_sale_order_id and not t.ke_account_payment_id):
            order = tx.ke_sale_order_id
            channel = order.ke_channel_id._api_env()
            if not tx.claimed:
                tx._claim(order.name)
            payment, _created = channel._api_register_payment(
                order.with_env(channel.env),
                {
                    "method": "mpesa",
                    "reference": tx.receipt_number or tx.checkout_request_id,
                    "amount": tx.amount,
                    "phone": tx.phone,
                },
                transaction=tx,
            )
        return res

    def _ke_on_failed(self):
        res = super()._ke_on_failed()
        Event = self.env["ke.ecommerce.webhook.event"]
        for tx in self.filtered("ke_sale_order_id"):
            order = tx.ke_sale_order_id
            channel = order.ke_channel_id._api_env()
            Event._enqueue(
                order.ke_channel_id,
                "payment.failed",
                {
                    "order": channel._api_order_ref(order),
                    "method": "mpesa",
                    "checkout_request_id": tx.checkout_request_id,
                    "status": tx.state,
                    "message": tx._ke_result_message(),
                },
            )
        return res

    @api.model
    def _ke_api_status_payload(self, tx):
        return {
            "id": tx.id,
            "status": tx.state,
            "checkout_request_id": tx.checkout_request_id,
            "amount": tx.amount,
            "phone": tx.phone_masked,
            "receipt_number": tx.receipt_number or None,
            "message": tx._ke_result_message(),
        }
