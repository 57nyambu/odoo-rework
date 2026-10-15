import json

from odoo.tests import HttpCase, tagged

from .common import c2b_payload, stk_callback_payload


@tagged("post_install", "-at_install", "ke")
class TestKeMpesaCallbacksHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env["ke.mpesa.account"].create(
            {
                "name": "HTTP till",
                "shortcode_type": "paybill",
                "business_shortcode": "600999",
                "callback_base_url": "https://tenant1.example.co.ke",
            }
        )
        cls.token = cls.account.callback_token

    def _post(self, path, payload):
        return self.url_open(path, data=json.dumps(payload), headers={"Content-Type": "application/json"})

    def test_stk_callback_endpoint(self):
        tx = self.env["ke.mpesa.transaction"].create(
            {
                "account_id": self.account.id,
                "kind": "stk",
                "amount": 100,
                "phone": "254712345678",
                "checkout_request_id": "ws_CO_HTTP_1",
            }
        )
        response = self._post(f"/ke/lipa/stk/{self.token}", stk_callback_payload("ws_CO_HTTP_1", receipt="SJK3HTTP01"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ResultCode"], 0)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.receipt_number, "SJK3HTTP01")

    def test_wrong_token_is_refused(self):
        response = self._post("/ke/lipa/stk/not-the-token", stk_callback_payload("ws_CO_X"))
        self.assertEqual(response.status_code, 403)

    def test_c2b_endpoints(self):
        response = self._post(f"/ke/lipa/c2b/validate/{self.token}", c2b_payload("SJK3C2BH01"))
        self.assertEqual(response.json()["ResultCode"], "0")
        response = self._post(f"/ke/lipa/c2b/confirm/{self.token}", c2b_payload("SJK3C2BH01"))
        self.assertEqual(response.status_code, 200)
        tx = self.env["ke.mpesa.transaction"].search([("receipt_number", "=", "SJK3C2BH01")])
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.amount, 250.0)

    def test_ip_allow_list(self):
        self.env["ir.config_parameter"].sudo().set_param("ke_mpesa.allowed_callback_ips", "196.201.214.0/24")
        response = self._post(f"/ke/lipa/c2b/confirm/{self.token}", c2b_payload("SJK3C2BH02"))
        self.assertEqual(response.status_code, 403)
        self.env["ir.config_parameter"].sudo().set_param("ke_mpesa.allowed_callback_ips", "127.0.0.1")
        response = self._post(f"/ke/lipa/c2b/confirm/{self.token}", c2b_payload("SJK3C2BH02"))
        self.assertEqual(response.status_code, 200)
