import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class MpesaWebhookController(http.Controller):

    def _json_response(self, data):
        return request.make_response(
            json.dumps(data), headers=[("Content-Type", "application/json")]
        )

    @http.route(
        "/ke_mpesa/callback",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def mpesa_callback(self, **kwargs):
        """Daraja STK Push callback.

        Uses type="http" rather than type="json" — Daraja posts a raw JSON
        body, not Odoo's internal JSON-RPC envelope, so it needs to be
        parsed manually here.

        Idempotent: safe to receive the same CheckoutRequestID more than
        once (Daraja retries on timeout).
        """
        try:
            payload = json.loads(request.httprequest.get_data())
        except (ValueError, TypeError):
            _logger.warning("M-Pesa callback: invalid JSON body")
            return self._json_response({"ResultCode": 1, "ResultDesc": "Invalid payload"})

        _logger.info("M-Pesa callback received: %s", payload)

        body = payload.get("Body", {}).get("stkCallback", {})
        checkout_request_id = body.get("CheckoutRequestID")
        result_code = body.get("ResultCode")

        if not checkout_request_id:
            return self._json_response(
                {"ResultCode": 1, "ResultDesc": "Missing CheckoutRequestID"}
            )

        transaction = (
            request.env["mpesa.transaction"]
            .sudo()
            .search([("checkout_request_id", "=", checkout_request_id)], limit=1)
        )
        if not transaction:
            _logger.warning("No matching mpesa.transaction for %s", checkout_request_id)
            return self._json_response({"ResultCode": 0, "ResultDesc": "Accepted"})

        # Idempotency guard: don't reprocess an already-settled transaction.
        if transaction.state in ("confirmed", "failed", "cancelled"):
            return self._json_response({"ResultCode": 0, "ResultDesc": "Already processed"})

        if result_code == 0:
            items = body.get("CallbackMetadata", {}).get("Item", [])
            receipt = next(
                (i["Value"] for i in items if i.get("Name") == "MpesaReceiptNumber"),
                None,
            )
            transaction.write(
                {
                    "state": "confirmed",
                    "mpesa_receipt_number": receipt,
                    "raw_callback": json.dumps(payload),
                }
            )
        else:
            transaction.write({"state": "failed", "raw_callback": json.dumps(payload)})

        return self._json_response({"ResultCode": 0, "ResultDesc": "Accepted"})
