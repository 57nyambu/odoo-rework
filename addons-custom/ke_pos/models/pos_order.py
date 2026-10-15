from collections import defaultdict

from odoo import api, models


class PosOrder(models.Model):
    _name = "pos.order"
    _inherit = ["pos.order", "ke.etims.mixin"]

    # ------------------------------------------------------------------
    # Sync from the POS UI
    # ------------------------------------------------------------------
    @api.model
    def _ke_server_owned_fields(self):
        """Fields only the server may write. The POS sends every stored
        field back when it syncs an order, which would otherwise reset the
        eTIMS status of an order."""
        return [name for name in self.env["ke.etims.mixin"]._fields if name.startswith("ke_etims_")]

    @api.model
    def _process_order(self, order, existing_order):
        for name in self._ke_server_owned_fields():
            order.pop(name, None)
        return super()._process_order(order, existing_order)

    def _process_saved_order(self, draft):
        order_id = super()._process_saved_order(draft)
        if not draft:
            self._ke_etims_queue()
            Tx = self.env["ke.mpesa.transaction"].sudo()
            # Payments are linked before the order gets its final name.
            Tx.search([("pos_order_id", "=", self.id)]).write({"claim_reference": self.name})
            # M-Pesa payments reserved by this order but not used in the end
            # (payment line removed) become available again.
            Tx.search(
                [("pos_order_uuid", "=", self.uuid), ("pos_payment_id", "=", False), ("claimed", "=", True)]
            )._ke_release_claim()
        return order_id

    @api.model
    def ke_etims_sign_pos_orders(self, order_ids):
        """Called by the POS right after the order is saved, before printing
        the receipt: transmit to eTIMS now (if not already done by the cron)
        and return the refreshed order data for the receipt."""
        orders = self.browse(order_ids).exists()
        orders.check_access("read")
        orders = orders.sudo()
        invoices = orders.account_move.filtered(lambda m: m.ke_etims_state in ("to_send", "error"))
        invoices._ke_etims_send()
        orders.filtered(lambda o: o.ke_etims_state in ("to_send", "error"))._ke_etims_send()
        orders._ke_etims_copy_from_invoice()
        config = orders.config_id[:1]
        return orders.with_env(self.env).read_pos_data([], config) if config else {}

    def _ke_etims_copy_from_invoice(self):
        """When the sale was invoiced, the invoice is the fiscal document;
        mirror its eTIMS data on the order so the receipt can print it."""
        for order in self.filtered(lambda o: o.account_move.ke_etims_state == "sent"):
            move = order.account_move
            order.write(
                {
                    name: move[name]
                    for name in self._ke_server_owned_fields()
                    if name not in ("ke_etims_raw_response",)
                }
            )

    def action_view_ke_mpesa_transactions(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("ke_mpesa.action_ke_mpesa_transaction")
        action.update({"domain": [("pos_order_id", "=", self.id)], "context": {}})
        return action

    # ------------------------------------------------------------------
    # eTIMS
    # ------------------------------------------------------------------
    def _ke_etims_is_applicable(self):
        self.ensure_one()
        return (
            self.company_id.ke_etims_enabled
            and self.state in ("paid", "done")
            and not self.account_move  # the invoice is the fiscal document
            and not self.company_id.currency_id.is_zero(self.amount_total)
        )

    def _ke_etims_payment_type(self):
        self.ensure_one()
        totals = defaultdict(float)
        for payment in self.payment_ids.filtered(lambda p: not p.is_change):
            totals[payment.payment_method_id.ke_etims_payment_type or "other"] += abs(payment.amount)
        if not totals:
            return "cash"
        return max(totals, key=totals.get)

    def _ke_etims_prepare_payload(self):
        self.ensure_one()
        lines = []
        # Combo parents carry no amount (their components do): skip them.
        for line in self.lines.filtered(
            lambda l: not (l.combo_line_ids and self.currency_id.is_zero(l.price_subtotal_incl))
        ):
            quantity = abs(line.qty)
            gross = abs(line.price_subtotal_incl)
            gross_before_discount = gross / (1 - line.discount / 100.0) if line.discount < 100 else 0.0
            lines.append(
                {
                    "product": line.product_id,
                    "name": line.full_product_name or line.product_id.display_name,
                    "quantity": quantity,
                    "unit_price": gross_before_discount / quantity if quantity else 0.0,
                    "discount_amount": gross_before_discount - gross,
                    "taxable_amount": abs(line.price_subtotal),
                    "tax_amount": gross - abs(line.price_subtotal),
                    "total_amount": gross,
                    "taxes": line.tax_ids_after_fiscal_position,
                }
            )
        is_refund = self.amount_total < 0
        original = None
        if is_refund and self.refunded_order_id:
            refunded = self.refunded_order_id
            original = refunded.ke_etims_receipt_number or refunded.ke_etims_invoice_number or refunded.pos_reference
        return self._ke_etims_build_payload(
            document_type="credit_note" if is_refund else "sale",
            source="pos",
            reference=self.pos_reference or self.name,
            date=self.date_order,
            partner=self.partner_id,
            currency=self.currency_id,
            exchange_rate=1.0,
            lines=lines,
            payment_type=self._ke_etims_payment_type(),
            original=original,
            reason=None,
        )
