"""Thin, stateless client for Safaricom's Daraja API (M-Pesa).

Kept free of ORM logic: `ke.mpesa.account` builds a client with its
credentials and decides what to do with the answers.
"""
import base64
import logging
from datetime import datetime

import pytz
import requests

_logger = logging.getLogger(__name__)

DARAJA_BASE_URL = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
TIMEOUT = (5, 20)  # connect, read

# Daraja refuses callback URLs containing these words (case-insensitive).
FORBIDDEN_URL_KEYWORDS = ("mpesa", "m-pesa", "safaricom", "exe", "exec", "cmd", "sql", "query")

# STK result codes worth explaining to a cashier / customer.
STK_RESULT_MESSAGES = {
    "0": "The payment was successful.",
    "1": "The customer's M-Pesa balance is insufficient.",
    "1001": "The customer has another M-Pesa transaction in progress. Try again in a minute.",
    "1019": "The payment request expired before the customer completed it.",
    "1025": "M-Pesa could not send the prompt to the customer's phone. Try again.",
    "1032": "The customer cancelled the payment on their phone.",
    "1037": "The customer's phone could not be reached (switched off or no network).",
    "2001": "The customer entered a wrong M-Pesa PIN.",
    "9999": "M-Pesa could not process the request. Try again.",
}


class DarajaError(Exception):
    """Raised for any failed Daraja call. ``code`` holds Daraja's errorCode
    or the HTTP status; ``message`` a human readable explanation."""

    def __init__(self, message, code=None, payload=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload


class DarajaClient:
    def __init__(self, environment, consumer_key, consumer_secret, token_getter=None, token_setter=None):
        if environment not in DARAJA_BASE_URL:
            raise DarajaError(f"Unknown Daraja environment {environment!r}")
        self.base_url = DARAJA_BASE_URL[environment]
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        # Optional cache hooks so the access token (valid 1 hour) is reused
        # across requests instead of fetched before every call.
        self.token_getter = token_getter
        self.token_setter = token_setter

    # ------------------------------------------------------------------
    @staticmethod
    def timestamp(now=None):
        now = now or datetime.now(tz=NAIROBI_TZ)
        return now.astimezone(NAIROBI_TZ).strftime("%Y%m%d%H%M%S")

    @staticmethod
    def password(shortcode, passkey, timestamp):
        return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()

    @staticmethod
    def check_callback_url(url):
        lowered = (url or "").lower()
        bad = [word for word in FORBIDDEN_URL_KEYWORDS if word in lowered]
        if bad:
            raise DarajaError(
                f"Safaricom rejects callback URLs containing {', '.join(bad)!r}: {url}"
            )
        if not lowered.startswith("https://"):
            _logger.warning("Daraja callback URL %s is not HTTPS; production requires HTTPS.", url)

    # ------------------------------------------------------------------
    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
        except requests.RequestException as error:
            raise DarajaError(f"Could not reach M-Pesa ({error.__class__.__name__}).") from error
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            message = data.get("errorMessage") or data.get("ResultDesc") or response.text[:200]
            raise DarajaError(message or f"HTTP {response.status_code}", data.get("errorCode") or response.status_code, data)
        return data

    def get_access_token(self, force=False):
        if not force and self.token_getter:
            token = self.token_getter()
            if token:
                return token
        if not (self.consumer_key and self.consumer_secret):
            raise DarajaError("The Daraja consumer key and secret are not configured.")
        data = self._request(
            "GET",
            "/oauth/v1/generate?grant_type=client_credentials",
            auth=(self.consumer_key, self.consumer_secret),
        )
        token = data.get("access_token")
        if not token:
            raise DarajaError("Daraja did not return an access token.", payload=data)
        if self.token_setter:
            self.token_setter(token, int(data.get("expires_in") or 3599))
        return token

    def _authorized(self, method, path, payload):
        token = self.get_access_token()
        try:
            return self._request(method, path, json=payload, headers={"Authorization": f"Bearer {token}"})
        except DarajaError as error:
            # Cached token revoked/expired early: refresh once.
            if str(error.code) in ("404.001.03", "401") or "Invalid Access Token" in error.message:
                token = self.get_access_token(force=True)
                return self._request(method, path, json=payload, headers={"Authorization": f"Bearer {token}"})
            raise

    # ------------------------------------------------------------------
    def stk_push(self, *, shortcode, passkey, transaction_type, party_b, phone, amount,
                 callback_url, account_reference, description):
        self.check_callback_url(callback_url)
        timestamp = self.timestamp()
        payload = {
            "BusinessShortCode": shortcode,
            "Password": self.password(shortcode, passkey, timestamp),
            "Timestamp": timestamp,
            "TransactionType": transaction_type,
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": party_b,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": (account_reference or "Payment")[:12],
            "TransactionDesc": (description or "Payment")[:13],
        }
        data = self._authorized("POST", "/mpesa/stkpush/v1/processrequest", payload)
        if str(data.get("ResponseCode")) != "0":
            raise DarajaError(data.get("ResponseDescription") or data.get("errorMessage") or "STK push rejected", data.get("ResponseCode"), data)
        return data

    def stk_query(self, *, shortcode, passkey, checkout_request_id):
        timestamp = self.timestamp()
        payload = {
            "BusinessShortCode": shortcode,
            "Password": self.password(shortcode, passkey, timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        return self._authorized("POST", "/mpesa/stkpushquery/v1/query", payload)

    def register_c2b_urls(self, *, shortcode, confirmation_url, validation_url, response_type="Completed"):
        self.check_callback_url(confirmation_url)
        self.check_callback_url(validation_url)
        payload = {
            "ShortCode": shortcode,
            "ResponseType": response_type,
            "ConfirmationURL": confirmation_url,
            "ValidationURL": validation_url,
        }
        return self._authorized("POST", "/mpesa/c2b/v1/registerurl", payload)
