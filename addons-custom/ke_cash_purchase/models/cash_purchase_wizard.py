from odoo import fields, models
from odoo.exceptions import UserError


class KeCashPurchaseLine(models.TransientModel):
    _name = "ke.cash.purchase.line"
    _description = "Cash purchase line"

    wizard_id = fields.Many2one(
        "ke.cash.purchase.wizard", required=True, ondelete="cascade"
    )
    product_id = fields.Many2one("product.product", required=True)
    quantity = fields.Float(default=1.0, required=True)
    unit_price = fields.Monetary(required=True)
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id
    )


class KeCashPurchaseWizard(models.TransientModel):
    _name = "ke.cash.purchase.wizard"
    _description = "Record a cash purchase in one step"

    partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        required=True,
        help=(
            "Use a generic 'Cash - walk-in supplier' contact if there's no "
            "formal vendor record for this purchase."
        ),
    )
    line_ids = fields.One2many("ke.cash.purchase.line", "wizard_id", string="Items")
    cash_journal_id = fields.Many2one(
        "account.journal",
        string="Cash journal",
        required=True,
        domain=[("type", "=", "cash")],
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        help="Leave empty to skip creating a stock receipt (e.g. for a service purchase).",
    )

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError("Add at least one line before confirming.")

        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner_id.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "quantity": line.quantity,
                            "price_unit": line.unit_price,
                        },
                    )
                    for line in self.line_ids
                ],
            }
        )
        bill.action_post()

        # account.payment.register is the standard high-level API for
        # registering + reconciling a payment against a posted bill —
        # don't hand-roll the reconciliation, it's easy to get wrong.
        payment_register = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=bill.ids
        ).create({"journal_id": self.cash_journal_id.id})
        payment_register.action_create_payments()

        self._ke_create_receipt(bill)
        return {"type": "ir.actions.act_window_close"}

    def _ke_create_receipt(self, bill):
        if not self.warehouse_id:
            return
        in_type = self.warehouse_id.in_type_id
        stock_lines = [line for line in self.line_ids if line.product_id.type != "service"]
        if not stock_lines:
            return

        picking = self.env["stock.picking"].create(
            {
                "partner_id": self.partner_id.id,
                "picking_type_id": in_type.id,
                "location_id": in_type.default_location_src_id.id,
                "location_dest_id": in_type.default_location_dest_id.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "name": line.product_id.display_name,
                            "product_id": line.product_id.id,
                            "product_uom_qty": line.quantity,
                            "product_uom": line.product_id.uom_id.id,
                            "location_id": in_type.default_location_src_id.id,
                            "location_dest_id": in_type.default_location_dest_id.id,
                        },
                    )
                    for line in stock_lines
                ],
            }
        )
        picking.action_confirm()
        picking.action_assign()
        # The "mark as done" field on stock.move/stock.move.line has been
        # renamed more than once across Odoo versions (qty_done →
        # quantity_done → quantity in different releases). Confirm the
        # current field name against your installed `stock` module
        # (`grep -rn "qty_done\|quantity_done" odoo/addons/stock/models/`)
        # before relying on this line — the ORM will raise a clear
        # AttributeError if it's wrong, which is easy to fix once you hit it.
        for move in picking.move_ids:
            move.quantity_done = move.product_uom_qty
        picking.button_validate()
