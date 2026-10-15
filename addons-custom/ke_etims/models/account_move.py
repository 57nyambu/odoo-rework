from odoo import fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "ke.etims.mixin"]

    ke_etims_show_send_button = fields.Boolean(compute="_compute_ke_etims_show_send_button")

    def _compute_ke_etims_show_send_button(self):
        for move in self:
            move.ke_etims_show_send_button = (
                move.state == "posted"
                and move.ke_etims_state in ("to_send", "error")
                and move.move_type in ("out_invoice", "out_refund")
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _ke_etims_is_applicable(self):
        self.ensure_one()
        return (
            self.company_id.ke_etims_enabled
            and self.state == "posted"
            and self.move_type in ("out_invoice", "out_refund")
        )

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._ke_etims_queue()
        return posted

    def button_draft(self):
        signed = self.filtered(lambda m: m.ke_etims_state == "sent")
        if signed:
            raise UserError(
                self.env._(
                    "%s has already been transmitted to KRA eTIMS and can no longer be modified. "
                    "Issue a credit note instead.",
                    ", ".join(signed.mapped("display_name")),
                )
            )
        res = super().button_draft()
        # Not transmitted yet: forget the queued transmission, it will be
        # queued again when the invoice is re-posted.
        self.filtered(lambda m: m.ke_etims_state in ("to_send", "error")).write(
            {"ke_etims_state": "not_applicable", "ke_etims_error": False, "ke_etims_attempts": 0}
        )
        return res

    # ------------------------------------------------------------------
    # Payload
    # ------------------------------------------------------------------
    def _ke_etims_payment_type(self):
        self.ensure_one()
        if self.payment_state in ("paid", "in_payment"):
            return "cash"
        return "credit"

    def _ke_etims_prepare_payload(self):
        self.ensure_one()
        lines = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            quantity = line.quantity
            gross_before_discount = (
                line.price_total / (1 - line.discount / 100.0) if line.discount < 100 else 0.0
            )
            lines.append(
                {
                    "product": line.product_id,
                    "name": line.name or line.product_id.display_name,
                    "quantity": quantity,
                    "unit_price": gross_before_discount / quantity if quantity else 0.0,
                    "discount_amount": gross_before_discount - line.price_total,
                    "taxable_amount": line.price_subtotal,
                    "tax_amount": line.price_total - line.price_subtotal,
                    "total_amount": line.price_total,
                    "taxes": line.tax_ids,
                }
            )
        original = reason = None
        if self.move_type == "out_refund":
            origin = self.reversed_entry_id
            original = origin.ke_etims_receipt_number or origin.ke_etims_invoice_number or origin.name or None
            reason = self.ref or None
        return self._ke_etims_build_payload(
            document_type="credit_note" if self.move_type == "out_refund" else "sale",
            source="invoice",
            reference=self.name,
            date=self.invoice_date or fields.Date.context_today(self),
            partner=self.partner_id,
            currency=self.currency_id,
            exchange_rate=self.invoice_currency_rate and 1.0 / self.invoice_currency_rate or 1.0,
            lines=lines,
            payment_type=self._ke_etims_payment_type(),
            original=original,
            reason=reason,
        )
