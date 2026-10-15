import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from odoo import Command
from odoo.tests import tagged

from odoo.addons.ke_mpesa.tests.common import DARAJA_REQUEST, fake_response, stk_callback_payload

from .common import KeEcommerceApiCommon


@tagged("post_install", "-at_install", "ke")
class TestKeEcommerceApi(KeEcommerceApiCommon):
    # ------------------------------------------------------------------
    # Authentication & conventions
    # ------------------------------------------------------------------
    def test_authentication_and_scopes(self):
        response, payload = self.call("GET", "/ping", key="ke_nope_nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")
        self.assertTrue(payload["error"]["request_id"])

        response = self.url_open("/api/ke/v1/ping")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["WWW-Authenticate"])

        response, payload = self.call("GET", "/orders", key=self.catalog_key)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "insufficient_scope")

        response, payload = self.call("GET", "/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["company"]["kra_pin"], "P051234567X")
        self.assertEqual(payload["company"]["currency"], "KES")
        self.assertTrue(response.headers.get("X-Request-Id"))

        self.key_record.action_revoke()
        response, _payload = self.call("GET", "/ping")
        self.assertEqual(response.status_code, 401)

    def test_rate_limit(self):
        self.channel.rate_limit_per_minute = 2
        statuses = [self.call("GET", "/ping")[0].status_code for _i in range(3)]
        self.assertEqual(statuses[-1], 429)

    def test_request_log(self):
        self.call("POST", "/orders", {"external_id": "BAD", "lines": []})
        log = self.env["ke.ecommerce.request.log"].search([("path", "=", "/api/ke/v1/orders")], limit=1)
        self.assertEqual(log.status, 422)
        self.assertEqual(log.error_code, "validation_error")
        self.assertIn('"BAD"', log.request_body)

    # ------------------------------------------------------------------
    # Catalog & stock
    # ------------------------------------------------------------------
    def test_products_and_stock(self):
        response, payload = self.call("GET", "/products")
        self.assertEqual(response.status_code, 200)
        skus = {p["sku"] for p in payload["data"]}
        self.assertEqual(skus, {"SODA-500", "UNGA-2KG"})
        soda = next(p for p in payload["data"] if p["sku"] == "SODA-500")
        self.assertAlmostEqual(soda["price"], 116.0)
        self.assertAlmostEqual(soda["price_excluding_tax"], 100.0)
        self.assertEqual(soda["etims_tax_type"], "B")
        self.assertEqual(soda["available_quantity"], 10.0)
        self.assertEqual(payload["meta"]["total"], 2)

        self.channel.stock_buffer = 2
        _response, payload = self.call("GET", "/stock?skus=SODA-500")
        self.assertEqual(payload["data"][0]["available_quantity"], 8.0)
        _response, payload = self.call("GET", f"/stock?skus=SODA-500&location_id={self.branch.code}")
        self.assertEqual(payload["data"][0]["available_quantity"], 1.0)

        response, _payload = self.call("GET", f"/products/{self.hidden.id}")
        self.assertEqual(response.status_code, 404)

        _response, payload = self.call("GET", "/counties")
        self.assertEqual(len(payload["data"]), 47)
        _response, payload = self.call("GET", "/pickup-locations")
        self.assertEqual(payload["data"][0]["code"], "WSTL")

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def test_order_lifecycle_paid_invoiced_etims(self):
        response, payload = self.call("POST", "/orders", self.order_payload())
        self.assertEqual(response.status_code, 201, payload)
        data = payload["data"]
        self.assertIn(data["status"], ("confirmed", "ready"))
        self.assertEqual(data["payment_status"], "unpaid")
        self.assertAlmostEqual(data["amount_total"], 2 * 116 + 180 + 200)
        self.assertEqual(data["customer"]["kra_pin"], "A012345678Z")
        self.assertEqual(data["shipping_address"]["county_code"], "KE-30")
        tax_types = {t["etims_tax_type"]: t for t in data["tax_breakdown"]}
        self.assertAlmostEqual(tax_types["A"]["tax"], 0.0)
        self.assertAlmostEqual(tax_types["B"]["tax"], 32.0 + 27.59, places=2)

        # idempotent replay
        response, replay = self.call("POST", "/orders", self.order_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["data"]["id"], data["id"])

        # stock is reserved for the online order
        _response, stock = self.call("GET", "/stock?skus=SODA-500")
        self.assertEqual(stock["data"][0]["available_quantity"], 8.0)

        response, paid = self.call(
            "POST",
            "/orders/WC-1001/payments",
            {"method": "mpesa", "reference": "sjk3web001", "amount": 612, "phone": "0712345678"},
        )
        self.assertEqual(response.status_code, 201, paid)
        order = paid["data"]["order"]
        self.assertEqual(order["payment_status"], "paid")
        self.assertEqual(paid["data"]["payment"]["reference"], "SJK3WEB001")
        self.assertEqual(len(order["invoices"]), 1)
        invoice = order["invoices"][0]
        self.assertEqual(invoice["state"], "posted")
        self.assertIn(invoice["payment_state"], ("paid", "in_payment"))
        self.assertEqual(invoice["etims"]["status"], "to_send")

        # same payment reported twice: no double booking
        response, replay = self.call(
            "POST", "/orders/WC-1001/payments", {"method": "mpesa", "reference": "SJK3WEB001", "amount": 612}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(replay["replayed"])

        move = self.env["account.move"].browse(invoice["id"])
        move._ke_etims_send()
        _response, fetched = self.call("GET", "/orders/WC-1001")
        etims = fetched["data"]["invoices"][0]["etims"]
        self.assertEqual(etims["status"], "sent")
        self.assertTrue(etims["cu_invoice_number"].startswith("TRAIN"))
        sale_order = self.env["sale.order"].browse(data["id"])
        self.assertEqual(sale_order.partner_id.vat, "A012345678Z")

    def test_order_validation_errors(self):
        response, payload = self.call("POST", "/orders", self.order_payload(lines=[{"sku": "HIDDEN", "quantity": 1}]))
        self.assertEqual((response.status_code, payload["error"]["code"]), (422, "unknown_product"))

        response, payload = self.call("POST", "/orders", self.order_payload(lines=[{"sku": "SODA-500", "quantity": 50}]))
        self.assertEqual((response.status_code, payload["error"]["code"]), (409, "insufficient_stock"))
        self.assertEqual(payload["error"]["details"]["items"][0]["available"], 10.0)

        bad_county = self.order_payload()
        bad_county["shipping_address"]["county"] = "Atlantis"
        response, payload = self.call("POST", "/orders", bad_county)
        self.assertEqual(payload["error"]["code"], "invalid_county")

        bad_pin = self.order_payload()
        bad_pin["customer"]["kra_pin"] = "123"
        response, payload = self.call("POST", "/orders", bad_pin)
        self.assertEqual(payload["error"]["code"], "invalid_kra_pin")

        response, payload = self.call("POST", "/orders", self.order_payload(currency="USD"))
        self.assertEqual(payload["error"]["code"], "currency_mismatch")
        # nothing was created by the failed calls
        self.assertFalse(self.env["sale.order"].search([("ke_channel_id", "=", self.channel.id)]))

    def test_price_source(self):
        payload = self.order_payload("WC-PRICE", lines=[{"sku": "SODA-500", "quantity": 1, "unit_price": 100}])
        response, result = self.call("POST", "/orders", payload)
        self.assertEqual(result["warnings"][0]["code"], "price_overridden")
        self.assertAlmostEqual(result["data"]["lines"][0]["total"], 116.0)

        self.channel.price_source = "platform"
        payload = self.order_payload("WC-PRICE-2", lines=[{"sku": "SODA-500", "quantity": 1, "unit_price": 100}])
        _response, result = self.call("POST", "/orders", payload)
        self.assertFalse(result["warnings"])
        self.assertAlmostEqual(result["data"]["lines"][0]["total"], 100.0)

    def test_pickup_order_and_cancel(self):
        payload = self.order_payload(
            "WC-PICKUP",
            fulfilment={"type": "pickup", "pickup_location_id": "WSTL"},
            lines=[{"sku": "SODA-500", "quantity": 3}],
        )
        payload.pop("shipping_address")
        response, result = self.call("POST", "/orders", payload)
        self.assertEqual(response.status_code, 201, result)
        self.assertEqual(result["data"]["fulfilment"]["pickup_location"]["code"], "WSTL")
        self.assertIsNone(result["data"]["shipping_address"])
        order = self.env["sale.order"].browse(result["data"]["id"])
        self.assertEqual(order.warehouse_id, self.branch)

        response, result = self.call("POST", "/orders/WC-PICKUP/cancel", {"reason": "Customer changed mind"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["data"]["status"], "cancelled")
        response, result = self.call("POST", "/orders/WC-PICKUP/payments", {"method": "card", "reference": "CARD-1", "amount": 10})
        self.assertEqual(result["error"]["code"], "order_cancelled")

    def test_cancel_paid_order_issues_credit_note(self):
        self.call("POST", "/orders", self.order_payload("WC-REFUND"))
        self.call("POST", "/orders/WC-REFUND/payments", {"method": "card", "reference": "CARD-REF-1", "amount": 612})
        response, result = self.call("POST", "/orders/WC-REFUND/cancel", {"reason": "Out of delivery area"})
        self.assertEqual(response.status_code, 200, result)
        types = sorted(i["type"] for i in result["data"]["invoices"])
        self.assertEqual(types, ["credit_note", "invoice"])

    # ------------------------------------------------------------------
    # M-Pesa STK push requested by the platform
    # ------------------------------------------------------------------
    def test_mpesa_stk_push_pays_order(self):
        account = self.env["ke.mpesa.account"].create(
            {
                "name": "Online paybill",
                "company_id": self.company.id,
                "shortcode_type": "paybill",
                "business_shortcode": "600100",
                "consumer_key": "k",
                "consumer_secret": "s",
                "passkey": "p",
                "callback_base_url": "https://tenant.example.co.ke",
            }
        )
        self.channel.mpesa_account_id = account
        _response, created = self.call("POST", "/orders", self.order_payload("WC-STK"))

        def daraja(method, url, **kwargs):
            if "/oauth/" in url:
                return fake_response(200, {"access_token": "t", "expires_in": "3599"})
            return fake_response(200, {"CheckoutRequestID": "ws_CO_API_1", "MerchantRequestID": "m", "ResponseCode": "0"})

        with patch(DARAJA_REQUEST, side_effect=daraja):
            response, result = self.call("POST", "/orders/WC-STK/mpesa-stk-push", {"phone": "0712345678"})
        self.assertEqual(response.status_code, 202, result)
        self.assertEqual(result["data"]["status"], "pending")
        self.assertEqual(result["data"]["amount"], 612.0)

        self.env["ke.mpesa.transaction"]._process_stk_callback(
            account, stk_callback_payload("ws_CO_API_1", receipt="SJK3STKAPI", amount=612)
        )
        _response, order = self.call("GET", "/orders/WC-STK")
        self.assertEqual(order["data"]["payment_status"], "paid")
        self.assertEqual(order["data"]["payments"][0]["reference"], "SJK3STKAPI")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    def test_webhooks_signed_and_retried(self):
        self.call("POST", "/orders", self.order_payload("WC-HOOK"))
        events = self.env["ke.ecommerce.webhook.event"].search([("channel_id", "=", self.channel.id)])
        self.assertIn("order.created", events.mapped("topic"))
        self.assertIn("order.confirmed", events.mapped("topic"))

        sent = []

        def post(url, data=None, headers=None, timeout=None):
            sent.append((url, data, headers))
            response = MagicMock()
            response.status_code = 200 if len(sent) > 1 else 500
            response.text = "ok"
            return response

        event = events.filtered(lambda e: e.topic == "order.created")
        with patch("odoo.addons.ke_ecommerce_api.models.ecommerce_webhook.requests.post", side_effect=post):
            event._dispatch()
            self.assertEqual(event.state, "pending")
            self.assertEqual(event.attempts, 1)
            event.next_attempt_at = False
            event._dispatch()
        self.assertEqual(event.state, "sent")

        url, body, headers = sent[-1]
        self.assertEqual(url, "https://shop.example.test/odoo-webhooks")
        timestamp, signature = (part.split("=", 1)[1] for part in headers["X-KE-Signature"].split(","))
        expected = hmac.new(self.channel.webhook_secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        envelope = json.loads(body)
        self.assertEqual(envelope["type"], "order.created")
        self.assertEqual(envelope["data"]["external_id"], "WC-HOOK")
        self.assertEqual(headers["X-KE-Event-Id"], envelope["id"])

        _response, listing = self.call("GET", "/webhooks/events?state=sent")
        self.assertEqual(listing["data"][0]["id"], event.uid)

    def test_product_update_event_debounced(self):
        self.soda.lst_price = 120
        self.soda.lst_price = 125
        events = self.env["ke.ecommerce.webhook.event"].search(
            [("topic", "=", "product.updated"), ("res_id", "=", self.soda.id)]
        )
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(json.loads(events.payload)["price"], 125.0)
        events.write({"state": "sent"})

        # removing the product from the channel tells the shop to hide it
        self.soda.product_tmpl_id.ke_ecommerce_channel_ids = [Command.unlink(self.channel.id)]
        event = self.env["ke.ecommerce.webhook.event"].search(
            [("topic", "=", "product.updated"), ("res_id", "=", self.soda.id), ("state", "=", "pending")]
        )
        self.assertEqual(len(event), 1)
        self.assertFalse(json.loads(event.payload)["published"])

    # ------------------------------------------------------------------
    # Multi-company tenancy
    # ------------------------------------------------------------------
    def test_company_isolation(self):
        self.call("POST", "/orders", self.order_payload("WC-ISO"))
        other_company = self.env["res.company"].create({"name": "Other Duka", "currency_id": self.company.currency_id.id})
        other_channel = self.env["ke.ecommerce.channel"].with_company(other_company).create(
            {
                "name": "Other shop",
                "code": "other-shop",
                "company_id": other_company.id,
                "warehouse_id": self.env["stock.warehouse"].search([("company_id", "=", other_company.id)], limit=1).id,
                "pricelist_id": self.env["product.pricelist"].create(
                    {"name": "Other", "currency_id": other_company.currency_id.id, "company_id": other_company.id}
                ).id,
            }
        )
        _record, other_key = self.env["ke.ecommerce.api.key"]._generate(
            other_channel, "other", self.env["ke.ecommerce.api.scope"].search([])
        )
        response, _payload = self.call("GET", "/orders/WC-ISO", key=other_key)
        self.assertEqual(response.status_code, 404)
        _response, payload = self.call("GET", "/products", key=other_key)
        self.assertEqual(payload["data"], [])
        self.assertEqual(other_channel.user_id.company_ids, other_company)
        self.assertNotIn(self.company, other_channel.user_id.company_ids)
