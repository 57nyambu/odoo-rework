from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "ke")
class TestCashPurchase(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("purchase.group_purchase_user") | cls.env.ref("stock.group_stock_user")
        cls.company = cls.company_data["company"]
        cls.vat16_purchase = cls.env["account.chart.template"].with_company(cls.company).ref("PT16")
        cls.sukuma = cls.env["product.product"].create(
            {
                "name": "Sukuma wiki (bunch)",
                "is_storable": True,
                "purchase_ok": True,
                "standard_price": 20.0,
                "supplier_taxes_id": [Command.set(cls.vat16_purchase.ids)],
            }
        )
        cls.supplier = cls.env["res.partner"].create({"name": "Mama Mboga (cash)"})
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)

    def _wizard(self, **vals):
        return self.env["ke.cash.purchase.wizard"].create(
            {
                "partner_id": self.supplier.id,
                "cash_journal_id": self.company_data["default_journal_cash"].id,
                "warehouse_id": self.warehouse.id,
                "line_ids": [Command.create({"product_id": self.sukuma.id, "quantity": 10, "unit_price": 25})],
                **vals,
            }
        )

    def test_cash_purchase_without_etims_has_no_vat(self):
        action = self._wizard(reference="R-889").action_confirm()
        order = self.env["purchase.order"].browse(action["res_id"])
        self.assertEqual(order.state, "purchase")
        self.assertFalse(order.order_line.tax_ids)
        self.assertEqual(order.picking_ids.state, "done")
        self.assertEqual(self.sukuma.with_context(warehouse_id=self.warehouse.id).qty_available, 10)
        bill = order.invoice_ids
        self.assertEqual(bill.state, "posted")
        self.assertAlmostEqual(bill.amount_total, 250.0)
        self.assertIn(bill.payment_state, ("paid", "in_payment"))

    def test_cash_purchase_with_etims_claims_vat(self):
        action = self._wizard(supplier_etims_invoice=True, supplier_etims_number="KRACU0300000005/77").action_confirm()
        order = self.env["purchase.order"].browse(action["res_id"])
        self.assertEqual(order.order_line.tax_ids, self.vat16_purchase)
        self.assertEqual(order.invoice_ids.ref, "KRACU0300000005/77")
        self.assertAlmostEqual(order.invoice_ids.amount_tax, 40.0)

    def test_etims_number_required(self):
        with self.assertRaises(UserError):
            self._wizard(supplier_etims_invoice=True).action_confirm()
