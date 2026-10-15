from contextlib import contextmanager
from unittest.mock import patch

from odoo.addons.ke_mpesa.tests.common import DARAJA_REQUEST, fake_response


class KePosMpesaMixin:
    """Daraja mock usable from any test class."""

    _checkout_counter = 0

    @contextmanager
    def mock_daraja(self):
        def _request(method, url, **kwargs):
            if "/oauth/" in url:
                return fake_response(200, {"access_token": "token", "expires_in": "3599"})
            if url.endswith("/stkpush/v1/processrequest"):
                type(self)._checkout_counter += 1
                return fake_response(
                    200,
                    {
                        "MerchantRequestID": "m-1",
                        "CheckoutRequestID": f"ws_CO_POS_{self._checkout_counter}",
                        "ResponseCode": "0",
                        "CustomerMessage": "Success. Request accepted for processing",
                    },
                )
            if url.endswith("/stkpushquery/v1/query"):
                return fake_response(500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"})
            return fake_response(404, {})

        with patch(DARAJA_REQUEST, side_effect=_request):
            yield
