from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class KeCashPurchaseLine(models.TransientModel):
    _name = "ke.cash.purchase.line"
    _description = "Cash purchase line"

    wizard_id = fields.Many2one("ke.cash.purchase.wizard", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True, domain=[("purchase_ok", "=", True)])
    quantity = fields.Float(default=1.0, required=True, digits="Product Unit")
    unit_price = fields.Monetary(required=True, help="Price paid per unit, as on the supplier's receipt.")
    currency_id = fields.Many2one(related="wizard_id.currency_id")
    subtotal = fields.Monetary(compute="_compute_subtotal")

    @api.depends("quantity", "unit_price")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.unit_price


class KeCashPurchaseWizard(models.TransientModel):
    """Record an informal, paid-on-the-spot purchase (market, jua kali,
    small suppliers) in one step, using the standard purchase flow so the
    purchase order, goods receipt, vendor bill and cash payment stay linked
    and auditable."""

    _name = "ke.cash.purchase.wizard"
    _description = "Record a cash purchase in one step"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        required=True,
        help="Use a generic 'Cash - walk-in supplier' contact if there's no formal vendor record.",
    )
    date = fields.Date(required=True, default=fields.Date.context_today)
    reference = fields.Char(string="Receipt number", help="Number on the supplier's receipt, if any.")
    supplier_etims_invoice = fields.Boolean(
        string="Supplier issued an eTIMS invoice",
        help="KRA only allows input VAT to be claimed on purchases backed by an eTIMS "
        "invoice in the company's KRA PIN. Without it, purchases are booked without VAT.",
    )
    supplier_etims_number = fields.Char(string="Supplier CU invoice number")
    line_ids = fields.One2many("ke.cash.purchase.line", "wizard_id", string="Items")
    cash_journal_id = fields.Many2one(
        "account.journal",
        string="Paid from",
        required=True,
        domain="[('type', 'in', ('cash', 'bank')), ('company_id', '=', company_id)]",
        default=lambda self: self.env["account.journal"].search(
            [("type", "=", "cash"), ("company_id", "=", self.env.company.id)], limit=1
        ),
        help="Cash box, or an M-Pesa / bank journal if paid by mobile money.",
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Receive into",
        domain="[('company_id', '=', company_id)]",
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1),
    )
    amount_total = fields.Monetary(compute="_compute_amount_total")

    @api.depends("line_ids.subtotal")
    def _compute_amount_total(self):
        for wizard in self:
            wizard.amount_total = sum(wizard.line_ids.mapped("subtotal"))

    def _purchase_order_vals(self):
        lines = []
        for line in self.line_ids:
            vals = {
                "product_id": line.product_id.id,
                "product_qty": line.quantity,
                "price_unit": line.unit_price,
                "product_uom_id": line.product_id.uom_id.id,
                "date_planned": fields.Datetime.now(),
            }
            if not self.supplier_etims_invoice:
                vals["tax_ids"] = [Command.clear()]
            lines.append(Command.create(vals))
        vals = {
            "partner_id": self.partner_id.id,
            "partner_ref": self.reference or False,
            "company_id": self.company_id.id,
            "date_order": fields.Datetime.now(),
            "order_line": lines,
        }
        if self.warehouse_id:
            vals["picking_type_id"] = self.warehouse_id.in_type_id.id
        return vals

    def action_confirm(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(self.env._("Add at least one item before confirming."))
        if self.supplier_etims_invoice and not self.supplier_etims_number:
            raise UserError(self.env._("Enter the supplier's eTIMS (CU) invoice number to claim input VAT."))

        order = self.env["purchase.order"].create(self._purchase_order_vals())
        order.button_confirm()
        self._receive(order)

        order.action_create_invoice()
        bills = order.invoice_ids.filtered(lambda m: m.state == "draft")
        bills.write(
            {
                "invoice_date": self.date,
                "ref": self.supplier_etims_number or self.reference or order.name,
            }
        )
        bills.action_post()
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=bills.ids
        ).create({"journal_id": self.cash_journal_id.id, "payment_date": self.date}).action_create_payments()

        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": order.id,
            "view_mode": "form",
            "target": "current",
        }

    def _receive(self, order):
        """Receive everything at once: the goods are in hand."""
        for picking in order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            result = picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
            if isinstance(result, dict):
                raise UserError(self.env._("The goods receipt %s needs manual validation.", picking.name))
