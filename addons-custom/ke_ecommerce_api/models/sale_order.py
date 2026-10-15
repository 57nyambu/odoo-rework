import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    ke_channel_id = fields.Many2one(
        "ke.ecommerce.channel", string="Online channel", index="btree_not_null", readonly=True, copy=False
    )
    ke_external_ref = fields.Char(string="Online order reference", index=True, readonly=True, copy=False)
    ke_fulfilment_type = fields.Selection(
        [("delivery", "Delivery"), ("pickup", "Pickup at branch")], string="Fulfilment", copy=False
    )
    ke_carrier = fields.Char(string="Courier", copy=False)
    ke_delivery_instructions = fields.Text(string="Delivery instructions", copy=False)
    ke_placed_at = fields.Datetime(string="Placed online at", copy=False, readonly=True)
    ke_payment_ids = fields.One2many("account.payment", "ke_sale_order_id", string="Online payments")
    ke_amount_paid = fields.Monetary(string="Paid online", compute="_compute_ke_payment_state", store=True)
    ke_amount_due = fields.Monetary(string="Due", compute="_compute_ke_payment_state", store=True)
    ke_payment_state = fields.Selection(
        [("unpaid", "Unpaid"), ("partial", "Partially paid"), ("paid", "Paid")],
        string="Online payment status",
        compute="_compute_ke_payment_state",
        store=True,
    )

    _ke_external_ref_uniq = models.Constraint(
        "unique(ke_channel_id, ke_external_ref)", "This online order was already imported."
    )

    @api.depends("amount_total", "ke_payment_ids.amount", "ke_payment_ids.state")
    def _compute_ke_payment_state(self):
        for order in self:
            payments = order.ke_payment_ids.filtered(lambda p: p.state in ("in_process", "paid"))
            paid = sum(
                payment.currency_id._convert(payment.amount, order.currency_id, order.company_id, payment.date)
                for payment in payments
            )
            currency = order.currency_id
            order.ke_amount_paid = paid
            order.ke_amount_due = max(order.amount_total - paid, 0.0)
            if currency.is_zero(paid):
                order.ke_payment_state = "unpaid"
            elif currency.compare_amounts(paid, order.amount_total) >= 0:
                order.ke_payment_state = "paid"
            else:
                order.ke_payment_state = "partial"

    def _ke_api_status(self):
        """pending / confirmed / ready / partially_fulfilled / fulfilled / cancelled"""
        self.ensure_one()
        if self.state == "cancel":
            return "cancelled"
        if self.state in ("draft", "sent"):
            return "pending"
        outgoing = self.picking_ids.filtered(lambda p: p.state != "cancel" and p.picking_type_code == "outgoing")
        if not outgoing:
            lines = self.order_line.filtered(lambda l: not l.display_type)
            delivered = all(l.qty_delivered >= l.product_uom_qty for l in lines) if lines else False
            return "fulfilled" if delivered and self.picking_ids else "confirmed"
        if all(p.state == "done" for p in outgoing):
            return "fulfilled"
        if any(p.state == "done" for p in outgoing):
            return "partially_fulfilled"
        if all(p.state == "assigned" for p in outgoing):
            return "ready"
        return "confirmed"

    def _ke_emit(self, topic):
        Event = self.env["ke.ecommerce.webhook.event"]
        for order in self.filtered("ke_channel_id"):
            channel = order.ke_channel_id._api_env()
            Event._enqueue(order.ke_channel_id, topic, channel._api_order_payload(order.with_env(channel.env)), order)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------
    def action_confirm(self):
        res = super().action_confirm()
        self.filtered("ke_channel_id")._ke_emit("order.confirmed")
        return res

    def _action_cancel(self):
        res = super()._action_cancel()
        self.filtered("ke_channel_id")._ke_emit("order.cancelled")
        return res

    def _ke_after_payment(self):
        for order in self.filtered("ke_channel_id"):
            if order.ke_payment_state == "paid":
                order._ke_emit("order.paid")
            order._ke_try_auto_invoice()
            order._ke_reconcile_online_payments()

    def _ke_try_auto_invoice(self):
        """Invoice fully paid online orders (posting the invoice queues it for
        KRA eTIMS). Orders invoiced on delivery are retried when the delivery
        is validated."""
        for order in self.filtered(
            lambda o: o.ke_channel_id.auto_invoice
            and o.state == "sale"
            and o.ke_payment_state == "paid"
            and o.invoice_status == "to invoice"
        ):
            try:
                with self.env.cr.savepoint():
                    invoices = order._create_invoices()
                    invoices.action_post()
            except UserError as error:
                _logger.info("Online order %s not invoiced yet: %s", order.name, error)

    def _ke_reconcile_online_payments(self):
        """Match payments received before the invoice existed."""
        for order in self.filtered("ke_payment_ids"):
            invoices = order.invoice_ids.filtered(
                lambda m: m.state == "posted" and m.move_type == "out_invoice" and m.amount_residual > 0
            )
            payments = order.ke_payment_ids.filtered(lambda p: p.state in ("in_process", "paid"))
            if not invoices or not payments:
                continue
            payment_lines = payments.sudo().move_id.line_ids.filtered(
                lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled
            )
            for invoice in invoices.sudo():
                invoice_lines = invoice.line_ids.filtered(
                    lambda l: l.account_id.account_type == "asset_receivable" and not l.reconciled
                )
                for account in invoice_lines.account_id:
                    to_reconcile = (invoice_lines + payment_lines).filtered(
                        lambda l: l.account_id == account and not l.reconciled
                    )
                    if len(to_reconcile) > 1:
                        to_reconcile.reconcile()
            payments.sudo().write({"invoice_ids": [(4, invoice.id) for invoice in invoices]})
