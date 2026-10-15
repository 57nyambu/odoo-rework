import base64

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.ke_mpesa.models.daraja_client import DarajaClient, DarajaError

from .common import KeMpesaCommon, c2b_payload, stk_callback_payload


@tagged("post_install", "-at_install", "ke")
class TestKeMpesa(KeMpesaCommon):
    def test_password_and_url_rules(self):
        password = DarajaClient.password("174379", "pk", "20261015102115")
        self.assertEqual(base64.b64decode(password).decode(), "174379pk20261015102115")
        self.assertEqual(len(DarajaClient.timestamp()), 14)
        with self.assertRaises(DarajaError):
            DarajaClient.check_callback_url("https://shop.co.ke/mpesa/callback")
        DarajaClient.check_callback_url(self.account.stk_callback_url)

    def test_stk_push_creates_pending_transaction(self):
        with self.mock_daraja() as calls:
            tx = self.account.stk_push("0712 345 678", 150.0, "POS-0001")
        self.assertEqual(tx.state, "pending")
        self.assertEqual(tx.phone, "254712345678")
        self.assertTrue(tx.checkout_request_id.startswith("ws_CO_TEST_"))
        stk_call = [c for c in calls if c[1].endswith("processrequest")][0]
        body = stk_call[2]["json"]
        self.assertEqual(body["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(body["PartyB"], "5123456")
        self.assertEqual(body["Amount"], 150)
        self.assertEqual(body["CallBackURL"], f"https://tenant1.example.co.ke/ke/lipa/stk/{self.account.callback_token}")
        # token cached: a second push does not request a new one
        with self.mock_daraja() as calls:
            self.account.stk_push("0712345678", 10, "POS-0002")
        self.assertFalse([c for c in calls if "/oauth/" in c[1]])

    def test_stk_push_validation(self):
        with self.mock_daraja():
            with self.assertRaises(UserError):
                self.account.stk_push("0812345678", 100, "X")
            with self.assertRaises(UserError):
                self.account.stk_push("0712345678", 99.5, "X")
            with self.assertRaises(UserError):
                self.account.stk_push("0712345678", 0, "X")

    def test_stk_push_daraja_error(self):
        with self.mock_daraja(stk_status=400, stk_payload={"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}):
            with self.assertRaisesRegex(UserError, "Invalid PhoneNumber"):
                self.account.stk_push("0712345678", 100, "X")

    def test_callback_confirms_once(self):
        with self.mock_daraja():
            tx = self.account.stk_push("0712345678", 100, "POS-1")
        Tx = self.env["ke.mpesa.transaction"]
        Tx._process_stk_callback(self.account, stk_callback_payload(tx.checkout_request_id))
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.receipt_number, "SJK3ABCD9Z")
        self.assertTrue(tx.transaction_date)
        # Safaricom retries: a later failure callback must not override it
        Tx._process_stk_callback(self.account, stk_callback_payload(tx.checkout_request_id, result_code=1032))
        self.assertEqual(tx.state, "confirmed")

    def test_callback_cancelled_by_user(self):
        with self.mock_daraja():
            tx = self.account.stk_push("0712345678", 100, "POS-2")
        self.env["ke.mpesa.transaction"]._process_stk_callback(
            self.account, stk_callback_payload(tx.checkout_request_id, result_code=1032)
        )
        self.assertEqual(tx.state, "cancelled")
        self.assertIn("cancelled", tx._ke_result_message())

    def test_orphan_callback_is_recorded(self):
        tx = self.env["ke.mpesa.transaction"]._process_stk_callback(
            self.account, stk_callback_payload("ws_CO_UNKNOWN", receipt="SJK3ORPHAN", amount=500)
        )
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.amount, 500)
        self.assertFalse(tx.claimed)

    def test_status_query_confirms(self):
        with self.mock_daraja():
            tx = self.account.stk_push("0712345678", 100, "POS-3")
            tx.last_query_at = False
            tx.action_query_status()
        self.assertEqual(tx.state, "confirmed")

    def test_c2b_confirmation_and_claim(self):
        Tx = self.env["ke.mpesa.transaction"]
        tx = Tx._process_c2b_confirmation(self.account, c2b_payload())
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.kind, "c2b")
        self.assertEqual(tx.payer_name, "Wanjiku K")
        # duplicate confirmation is ignored
        self.assertEqual(Tx._process_c2b_confirmation(self.account, c2b_payload()), tx)
        found = Tx._find_claimable("sjk3abcd9z", self.account)
        self.assertEqual(found, tx)
        found._claim("Shop/0001")
        self.assertTrue(tx.claimed)
        with self.assertRaisesRegex(UserError, "already been used"):
            found._claim("Shop/0002")

    def test_c2b_validation_rejects_zero(self):
        accepted, code = self.env["ke.mpesa.transaction"]._c2b_validate(self.account, c2b_payload(amount="0"))
        self.assertFalse(accepted)
        self.assertNotEqual(code, "0")

    def test_users_cannot_write_transactions(self):
        user = self.env["res.users"].create(
            {
                "name": "Viewer",
                "login": "mpesa_viewer",
                "group_ids": [(6, 0, [self.env.ref("base.group_user").id, self.env.ref("ke_mpesa.group_mpesa_user").id])],
            }
        )
        tx = self.env["ke.mpesa.transaction"]._process_c2b_confirmation(self.account, c2b_payload("SJK3WRITE1"))
        tx_as_user = tx.with_user(user)
        self.assertEqual(tx_as_user.state, "confirmed")
        from odoo.exceptions import AccessError

        with self.assertRaises(AccessError):
            tx_as_user.write({"state": "pending"})
