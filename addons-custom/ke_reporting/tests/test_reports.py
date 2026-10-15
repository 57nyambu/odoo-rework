from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install", "ke")
class TestKeReports(TestPoSCommon):
    @classmethod
    @TestPoSCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.vat16 = cls.env["account.chart.template"].with_company(cls.company).ref("ST16")
        cls.exempt = cls.env["account.chart.template"].with_company(cls.company).ref("STEX")
        cls.soda = cls.create_product("Soda", cls.categ_basic, 100.0, tax_ids=cls.vat16.ids)
        cls.unga = cls.create_product("Unga", cls.categ_basic, 180.0, tax_ids=cls.exempt.ids)
        cls.config = cls.basic_config

    def test_collections_and_vat_by_tax_type(self):
        self.open_new_session()
        orders = [
            self.create_ui_order_data([(self.soda, 2)]),
            self.create_ui_order_data([(self.unga, 1)], payments=[(self.bank_pm1, 180.0)]),
        ]
        self.env["pos.order"].sync_from_ui(orders)
        self.env.flush_all()

        Collections = self.env["ke.daily.sales.report"]
        rows = Collections.search([("config_id", "=", self.config.id)])
        by_method = {row.payment_method_id: row.amount for row in rows}
        self.assertAlmostEqual(by_method[self.cash_pm1], 232.0)
        self.assertAlmostEqual(by_method[self.bank_pm1], 180.0)

        vat = self.env["ke.vat.sales.report"].search([("config_id", "=", self.config.id)])
        by_type = {row.etims_tax_type: row for row in vat}
        self.assertAlmostEqual(by_type["B"].taxable_amount, 200.0)
        self.assertAlmostEqual(by_type["B"].tax_amount, 32.0)
        self.assertAlmostEqual(by_type["A"].tax_amount, 0.0)
        self.assertAlmostEqual(by_type["A"].taxable_amount, 180.0)
