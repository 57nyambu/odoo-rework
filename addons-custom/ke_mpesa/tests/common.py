import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase

DARAJA_REQUEST = "odoo.addons.ke_mpesa.models.daraja_client.requests.request"


def fake_response(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload or {}
    response.text = json.dumps(payload or {})
    return response


def stk_callback_payload(checkout_id, result_code=0, receipt="SJK3ABCD9Z", amount=100, phone=254712345678):
    body = {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": checkout_id,
        "ResultCode": result_code,
        "ResultDesc": "The service request is processed successfully." if result_code == 0 else "Request cancelled by user",
    }
    if result_code == 0:
        body["CallbackMetadata"] = {
            "Item": [
                {"Name": "Amount", "Value": amount},
                {"Name": "MpesaReceiptNumber", "Value": receipt},
                {"Name": "TransactionDate", "Value": 20261015102115},
                {"Name": "PhoneNumber", "Value": phone},
            ]
        }
    return {"Body": {"stkCallback": body}}


def c2b_payload(code="SJK3ABCD9Z", amount="250.00", shortcode="174379"):
    return {
        "TransactionType": "Buy Goods",
        "TransID": code,
        "TransTime": "20261015102115",
        "TransAmount": amount,
        "BusinessShortCode": shortcode,
        "BillRefNumber": "",
        "MSISDN": "254712345678",
        "FirstName": "Wanjiku",
        "MiddleName": "",
        "LastName": "K",
    }


class KeMpesaCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env["ke.mpesa.account"].create(
            {
                "name": "Test till",
                "environment": "sandbox",
                "shortcode_type": "till",
                "business_shortcode": "174379",
                "till_number": "5123456",
                "consumer_key": "key",
                "consumer_secret": "secret",
                "passkey": "passkey",
                "callback_base_url": "https://tenant1.example.co.ke",
            }
        )
        cls.checkout_counter = 0

    @contextmanager
    def mock_daraja(self, stk_status=200, stk_payload=None, query_payload=None):
        calls = []

        def _request(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if "/oauth/" in url:
                return fake_response(200, {"access_token": "token-123", "expires_in": "3599"})
            if url.endswith("/stkpush/v1/processrequest"):
                type(self).checkout_counter += 1
                return fake_response(
                    stk_status,
                    stk_payload
                    or {
                        "MerchantRequestID": "29115-34620561-1",
                        "CheckoutRequestID": f"ws_CO_TEST_{self.checkout_counter}",
                        "ResponseCode": "0",
                        "ResponseDescription": "Success. Request accepted for processing",
                        "CustomerMessage": "Success. Request accepted for processing",
                    },
                )
            if url.endswith("/stkpushquery/v1/query"):
                return fake_response(200, query_payload or {"ResultCode": "0", "ResultDesc": "OK"})
            if url.endswith("/c2b/v1/registerurl"):
                return fake_response(200, {"ResponseCode": "0", "ResponseDescription": "Success"})
            return fake_response(404, {"errorMessage": "unexpected URL"})

        with patch(DARAJA_REQUEST, side_effect=_request):
            yield calls
