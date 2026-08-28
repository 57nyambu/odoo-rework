import base64
import logging
from datetime import datetime

import requests

from odoo import models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DARAJA_BASE_URL = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}


class KeMpesaDarajaClient(models.AbstractModel):
    """Thin wrapper around Safaricom's Daraja API.

    The POS payment method calls `stk_push`; the async result arrives at
    the webhook controller (controllers/mpesa_webhook.py), which is the
    only other place that touches mpesa.transaction's state.
    """

    _name = "ke.mpesa.daraja"
    _description = "Daraja API client"

    def _get_access_token(self, payment_method):
        base_url = DARAJA_BASE_URL[payment_method.ke_mpesa_environment]
        auth = (
            payment_method.ke_mpesa_consumer_key,
            payment_method.ke_mpesa_consumer_secret,
        )
        resp = requests.get(
            f"{base_url}/oauth/v1/generate?grant_type=client_credentials",
            auth=auth,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def stk_push(self, payment_method, phone, amount, account_reference, callback_url):
        """Initiate an STK Push.

        Returns the raw Daraja response dict, which includes
        CheckoutRequestID — the caller is responsible for creating the
        mpesa.transaction record with that ID before returning control to
        the POS screen, so the webhook has something to match against.
        """
        base_url = DARAJA_BASE_URL[payment_method.ke_mpesa_environment]
        token = self._get_access_token(payment_method)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{payment_method.ke_mpesa_shortcode}"
            f"{payment_method.ke_mpesa_passkey}{timestamp}".encode()
        ).decode()

        payload = {
            "BusinessShortCode": payment_method.ke_mpesa_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": payment_method.ke_mpesa_shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": "POS payment",
        }
        resp = requests.post(
            f"{base_url}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code != 200:
            _logger.error("Daraja STK push failed: %s", resp.text)
            raise UserError("Could not reach M-Pesa. Try again, or take cash.")
        return resp.json()
