from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "ke")
class TestAllowedShops(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Config = cls.env["pos.config"]
        cls.shop_a = Config.create({"name": "Moi Avenue"})
        cls.shop_b = Config.create({"name": "Westlands"})
        base_user = cls.env.ref("base.group_user")
        cls.cashier = cls.env["res.users"].create(
            {
                "name": "Cashier",
                "login": "ke_cashier",
                "group_ids": [(6, 0, [base_user.id, cls.env.ref("ke_uac.group_ke_cashier").id])],
            }
        )
        cls.manager = cls.env["res.users"].create(
            {
                "name": "Branch manager",
                "login": "ke_branch_manager",
                "group_ids": [(6, 0, [base_user.id, cls.env.ref("ke_uac.group_ke_branch_manager").id])],
            }
        )

    def test_unrestricted_by_default(self):
        visible = self.env["pos.config"].with_user(self.cashier).search([])
        self.assertIn(self.shop_a, visible)
        self.assertIn(self.shop_b, visible)

    def test_restricted_cashier(self):
        self.cashier.ke_pos_config_ids = self.shop_a
        visible = self.env["pos.config"].with_user(self.cashier).search([])
        self.assertIn(self.shop_a, visible)
        self.assertNotIn(self.shop_b, visible)

    def test_manager_sees_everything(self):
        self.manager.ke_pos_config_ids = self.shop_a
        visible = self.env["pos.config"].with_user(self.manager).search([])
        self.assertIn(self.shop_b, visible)
        self.assertTrue(self.manager.has_group("point_of_sale.group_pos_manager"))
        self.assertTrue(self.manager.has_group("stock.group_stock_manager"))
