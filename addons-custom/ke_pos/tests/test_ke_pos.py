from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.ke_mpesa.tests.common import c2b_payload, stk_callback_payload
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from .common import KePosMpesaMixin


@tagged("post_install", "-at_install", "ke")
class TestKePos(KePosMpesaMixin, TestPoSCommon):
    @classmethod
    @TestPoSCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.company.write(
            {
                "vat": "P051234567X",
                "ke_etims_enabled": True,
                "ke_etims_connector": "demo",
                "ke_etims_environment": "sandbox",
            }
        )
        cls.vat16 = cls.env["account.chart.template"].with_company(cls.company).ref("ST16")
        cls.soda = cls.create_product("Soda 500ml", cls.categ_basic, 100.0, tax_ids=cls.vat16.ids)
        cls.mpesa_account = cls.env["ke.mpesa.account"].create(
            {
                "name": "Shop till",
                "shortcode_type": "till",
                "business_shortcode": "174379",
                "till_number": "5123456",
                "consumer_key": "key",
                "consumer_secret": "secret",
                "passkey": "passkey",
                "callback_base_url": "https://shop.example.co.ke",
            }
        )
        action = cls.mpesa_account.action_ke_create_pos_payment_method()
        cls.mpesa_pm = cls.env["pos.payment.method"].browse(action["res_id"])
        cls.config = cls.basic_config
        cls.config.payment_method_ids = [(4, cls.mpesa_pm.id)]

    def setUp(self):
        super().setUp()
        self.open_new_session()

    def _sync(self, order_data):
        result = self.env["pos.order"].sync_from_ui([order_data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    # ------------------------------------------------------------------
    def test_payment_method_setup(self):
        self.assertEqual(self.mpesa_pm.use_payment_terminal, "ke_mpesa")
        self.assertEqual(self.mpesa_pm.journal_id.type, "bank")
        self.assertEqual(self.mpesa_pm.ke_etims_payment_type, "mobile_money")
        self.assertEqual(self.config.ke_lipa_na_mpesa_text, "Lipa na M-Pesa - Buy Goods Till No. 5123456")
        fields = self.env["pos.payment.method"]._load_pos_data_fields(self.config)
        self.assertIn("ke_mpesa_display_number", fields)
        self.assertIn("ke_etims_enabled", self.env["res.company"]._load_pos_data_fields(self.config))

    def test_cash_sale_signed_before_printing(self):
        order = self._sync(self.create_ui_order_data([(self.soda, 2)]))
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.ke_etims_state, "to_send")
        data = self.env["pos.order"].ke_etims_sign_pos_orders(order.ids)
        self.assertEqual(order.ke_etims_state, "sent")
        self.assertTrue(order.ke_etims_invoice_number.startswith("TRAIN"))
        record = data["pos.order"][0]
        self.assertEqual(record["ke_etims_invoice_number"], order.ke_etims_invoice_number)
        self.assertTrue(record["ke_etims_qr_data"])
        payload = order._ke_etims_prepare_payload()
        self.assertEqual(payload["source"], "pos")
        self.assertEqual(payload["payment_type"], "cash")
        self.assertAlmostEqual(payload["totals"]["taxable_B"], 200.0)
        self.assertAlmostEqual(payload["totals"]["tax_B"], 32.0)

    def test_pos_cannot_overwrite_etims_fields(self):
        data = self.create_ui_order_data([(self.soda, 1)])
        data.update({"ke_etims_state": False, "ke_etims_invoice_number": "FAKE/1"})
        order = self._sync(data)
        self.assertEqual(order.ke_etims_state, "to_send")
        self.assertFalse(order.ke_etims_invoice_number)

    def test_mpesa_stk_sale(self):
        with self.mock_daraja():
            status = self.mpesa_pm.ke_mpesa_pos_stk_push("0712 345 678", 116, "uuid-stk-1", "Order 0001")
        self.assertEqual(status["state"], "pending")
        tx = self.env["ke.mpesa.transaction"].browse(status["id"])
        self.env["ke.mpesa.transaction"]._process_stk_callback(
            self.mpesa_account, stk_callback_payload(tx.checkout_request_id, receipt="SJK3STK001", amount=116)
        )
        status = self.mpesa_pm.ke_mpesa_pos_check_status(tx.id)
        self.assertEqual(status["state"], "confirmed")
        self.assertEqual(status["receipt_number"], "SJK3STK001")
        self.assertTrue(tx.claimed)

        data = self.create_ui_order_data([(self.soda, 1)], payments=[(self.mpesa_pm, 116)], uuid="uuid-stk-1")
        data["payment_ids"][0][2].update(
            {"transaction_id": "SJK3STK001", "payment_ref_no": tx.checkout_request_id, "payment_status": "done"}
        )
        order = self._sync(data)
        self.assertEqual(order.state, "paid")
        self.assertEqual(tx.pos_order_id, order)
        self.assertEqual(tx.pos_payment_id, order.payment_ids)
        self.assertEqual(order._ke_etims_payment_type(), "mobile_money")

    def test_mpesa_code_claim_rules(self):
        Tx = self.env["ke.mpesa.transaction"]
        tx = Tx._process_c2b_confirmation(self.mpesa_account, c2b_payload("SJK3C2B001", amount="116.00"))
        status = self.mpesa_pm.ke_mpesa_pos_claim_code("sjk3c2b001", "uuid-a")
        self.assertEqual(status["amount"], 116.0)
        # the same order may re-enter its own code (line deleted and re-added)
        self.mpesa_pm.ke_mpesa_pos_claim_code("SJK3C2B001", "uuid-a")
        with self.assertRaisesRegex(UserError, "already been used"):
            self.mpesa_pm.ke_mpesa_pos_claim_code("SJK3C2B001", "uuid-b")
        with self.assertRaisesRegex(UserError, "has reached"):
            self.mpesa_pm.ke_mpesa_pos_claim_code("SJK3NOPE01", "uuid-b")
        with self.assertRaises(UserError):
            self.mpesa_pm.ke_mpesa_pos_claim_code("BAD", "uuid-b")
        # order uuid-a finally paid in cash: the reserved M-Pesa payment is freed
        self._sync(self.create_ui_order_data([(self.soda, 1)], uuid="uuid-a"))
        self.assertFalse(tx.claimed)
        self.mpesa_pm.ke_mpesa_pos_claim_code("SJK3C2B001", "uuid-b")
        self.assertTrue(tx.claimed)

    def test_mpesa_transaction_of_other_account_refused(self):
        other = self.env["ke.mpesa.account"].create(
            {"name": "Other", "shortcode_type": "paybill", "business_shortcode": "600111"}
        )
        tx = self.env["ke.mpesa.transaction"]._process_c2b_confirmation(other, c2b_payload("SJK3OTHER1"))
        with self.assertRaises(UserError):
            self.mpesa_pm.ke_mpesa_pos_check_status(tx.id)

    def test_refund_is_credit_note(self):
        order = self._sync(self.create_ui_order_data([(self.soda, 1)]))
        self.env["pos.order"].ke_etims_sign_pos_orders(order.ids)
        refund = self.env["pos.order"].browse(order.refund()["res_id"])
        self.env["pos.make.payment"].with_context(active_id=refund.id, active_ids=refund.ids).create(
            {"payment_method_id": self.cash_pm1.id, "amount": refund.amount_total}
        ).check()
        self.assertEqual(refund.ke_etims_state, "to_send")
        payload = refund._ke_etims_prepare_payload()
        self.assertEqual(payload["document_type"], "credit_note")
        self.assertEqual(payload["original_reference"], order.ke_etims_receipt_number)
        self.assertAlmostEqual(payload["totals"]["total_amount"], 116.0)

    def test_invoiced_sale_fiscalized_once(self):
        customer = self.env["res.partner"].create(
            {"name": "Biashara Ltd", "country_id": self.env.ref("base.ke").id, "vat": "P000999888Z", "is_company": True}
        )
        order = self._sync(self.create_ui_order_data([(self.soda, 1)], customer=customer, is_invoiced=True))
        self.assertTrue(order.account_move)
        self.assertEqual(order.ke_etims_state, "not_applicable")
        self.assertEqual(order.account_move.ke_etims_state, "to_send")
        self.env["pos.order"].ke_etims_sign_pos_orders(order.ids)
        self.assertEqual(order.account_move.ke_etims_state, "sent")
        self.assertEqual(order.ke_etims_state, "sent")
        self.assertEqual(order.ke_etims_invoice_number, order.account_move.ke_etims_invoice_number)
        self.assertEqual(order.account_move._ke_etims_prepare_payload()["buyer"]["pin"], "P000999888Z")
