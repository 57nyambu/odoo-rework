"""REST API exchanged with e-commerce platforms. See docs/ECOMMERCE_API.md.

Conventions
-----------
* Base path ``/api/ke/v1``; JSON in, JSON out; amounts in the channel
  currency (KES); timestamps ISO 8601 in UTC.
* ``Authorization: Bearer <api key>`` on every call. The tenant database
  is selected by the host name (one sub-domain per tenant) or the
  ``X-Odoo-Database`` header.
* Errors: ``{"error": {"code", "message", "details", "request_id"}}``.
"""
import json
import logging
import threading
import time
import uuid

from psycopg2 import errors as pg_errors

from odoo import fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.fields import Domain
from odoo.http import Response, request

from odoo.addons.ke_ecommerce_api.models.api_tools import (
    API_VERSION,
    ApiError,
    format_datetime,
    page_meta,
    page_params,
    parse_datetime,
)

_logger = logging.getLogger(__name__)

PREFIX = "/api/ke/v1"
RETRYABLE_ERRORS = (pg_errors.LockNotAvailable, pg_errors.SerializationFailure, pg_errors.DeadlockDetected)

_rate_lock = threading.Lock()
_rate_windows = {}


def _consume_rate(key_id, limit):
    """Fixed one-minute window per API key and worker process. Put a global
    limit in the reverse proxy for multi-worker deployments (see docs)."""
    minute = int(time.time() // 60)
    with _rate_lock:
        window, count = _rate_windows.get(key_id, (minute, 0))
        if window != minute:
            window, count = minute, 0
        count += 1
        _rate_windows[key_id] = (window, count)
    return count <= limit, max(limit - count, 0), (window + 1) * 60


class KeEcommerceApiController(http.Controller):
    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------
    def _json(self, payload, status=200, headers=None):
        all_headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-KE-API-Version", API_VERSION),
        ] + list(headers or [])
        return request.make_response(json.dumps(payload, default=str), headers=all_headers, status=status)

    def _body(self):
        raw = request.httprequest.get_data(cache=True)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as error:
            raise ApiError(400, "invalid_json", "The request body is not valid JSON.") from error

    def _authenticate(self):
        header = request.httprequest.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        token = token or request.httprequest.headers.get("X-API-Key", "").strip()
        if not token:
            raise ApiError(401, "unauthorized", "Missing API key: send 'Authorization: Bearer <key>'.")
        key = request.env["ke.ecommerce.api.key"].sudo()._authenticate(token)
        if not key:
            raise ApiError(401, "unauthorized", "Invalid, revoked or expired API key.")
        return key, key.channel_id

    def _dispatch(self, scope, handler):
        """Authenticate, authorize, run ``handler(channel)`` in a savepoint and
        turn every outcome into a JSON response + a request log line."""
        started = time.monotonic()
        httprequest = request.httprequest
        request_id = httprequest.headers.get("X-Request-Id") or uuid.uuid4().hex
        key = channel = None
        status, error_code = 200, None
        extra_headers = [("X-Request-Id", request_id)]
        try:
            key, channel = self._authenticate()
            if scope and not key._has_scope(scope):
                raise ApiError(403, "insufficient_scope", f"This API key does not have the '{scope}' scope.")
            allowed, remaining, reset_at = _consume_rate(key.id, channel.rate_limit_per_minute or 120)
            extra_headers += [("X-RateLimit-Limit", str(channel.rate_limit_per_minute or 120)),
                              ("X-RateLimit-Remaining", str(remaining)),
                              ("X-RateLimit-Reset", str(reset_at))]
            if not allowed:
                extra_headers.append(("Retry-After", str(max(reset_at - int(time.time()), 1))))
                raise ApiError(429, "rate_limited", "Too many requests: slow down and retry later.")
            key._touch(httprequest.remote_addr)
            with request.env.cr.savepoint():
                result = handler(channel._api_env())
            if isinstance(result, Response):
                for name, value in extra_headers:
                    result.headers[name] = value
                response = result
            else:
                payload, status = result if isinstance(result, tuple) else (result, 200)
                response = self._json(payload, status, extra_headers)
        except RETRYABLE_ERRORS:
            raise  # let Odoo retry the whole request
        except ApiError as error:
            status, error_code = error.status, error.code
            response = self._error(error.status, error.code, error.message, error.details, request_id, extra_headers)
        except AccessError:  # before UserError: AccessError subclasses it
            status, error_code = 403, "forbidden"
            response = self._error(403, error_code, "Access denied.", None, request_id, extra_headers)
        except MissingError:
            status, error_code = 404, "not_found"
            response = self._error(404, error_code, "Record not found.", None, request_id, extra_headers)
        except (ValidationError, UserError) as error:
            status, error_code = 422, "validation_error"
            response = self._error(422, error_code, str(error.args[0] if error.args else error), None,
                                   request_id, extra_headers)
        except Exception:
            _logger.exception("E-commerce API error (request %s)", request_id)
            status, error_code = 500, "server_error"
            response = self._error(500, error_code, "Unexpected server error. Quote the request_id to support.",
                                   None, request_id, extra_headers)
        self._log(request_id, channel, key, status, error_code, started)
        return response

    def _error(self, status, code, message, details, request_id, headers):
        if status == 401:
            headers = list(headers) + [("WWW-Authenticate", 'Bearer realm="odoo-kenya"')]
        return self._json(
            {"error": {"code": code, "message": message, "details": details, "request_id": request_id}},
            status,
            headers,
        )

    def _log(self, request_id, channel, key, status, error_code, started):
        httprequest = request.httprequest
        body = None
        if status >= 400 and httprequest.method in ("POST", "PUT", "PATCH"):
            body = (httprequest.get_data(cache=True) or b"")[:4000].decode("utf-8", "replace")
        try:
            with request.env.cr.savepoint():
                request.env["ke.ecommerce.request.log"].sudo().create(
                    {
                        "request_id": request_id,
                        "channel_id": channel.id if channel else False,
                        "api_key_id": key.id if key else False,
                        "method": httprequest.method,
                        "path": httprequest.path[:250],
                        "status": status,
                        "error_code": error_code,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "ip_address": httprequest.remote_addr,
                        "request_body": body,
                    }
                )
        except Exception:  # noqa: BLE001 - logging must never break the API
            _logger.exception("Could not write the e-commerce API request log")

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    @http.route(f"{PREFIX}/ping", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def ping(self, **kwargs):
        def handler(channel):
            company = channel.company_id
            return {
                "status": "ok",
                "api_version": API_VERSION,
                "database": request.db,
                "server_time": format_datetime(fields.Datetime.now()),
                "channel": {"code": channel.code, "name": channel.name},
                "company": {"name": company.name, "kra_pin": company.vat or None, "currency": company.currency_id.name},
            }

        return self._dispatch(None, handler)

    @http.route(f"{PREFIX}/counties", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def counties(self, **kwargs):
        return self._dispatch(None, lambda channel: {"data": channel._api_counties()})

    @http.route(f"{PREFIX}/categories", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def categories(self, **kwargs):
        return self._dispatch("catalog:read", lambda channel: {"data": channel._api_categories()})

    @http.route(f"{PREFIX}/pickup-locations", type="http", auth="public", methods=["GET"], csrf=False,
                save_session=False)
    def pickup_locations(self, **kwargs):
        def handler(channel):
            return {"data": [channel._api_warehouse_payload(w) for w in channel.pickup_warehouse_ids]}

        return self._dispatch("catalog:read", handler)

    # ------------------------------------------------------------------
    # Catalog & stock
    # ------------------------------------------------------------------
    @http.route(f"{PREFIX}/products", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def products(self, **params):
        def handler(channel):
            page, size = page_params(params)
            products, total = channel._api_search_products(params, page, size)
            available = channel._api_available_qty(products)
            return {
                "data": [channel._api_product_payload(p, available[p.id]) for p in products],
                "meta": page_meta(page, size, total),
            }

        return self._dispatch("catalog:read", handler)

    @http.route(f"{PREFIX}/products/<int:product_id>", type="http", auth="public", methods=["GET"], csrf=False,
                save_session=False)
    def product(self, product_id, **kwargs):
        def handler(channel):
            product = channel._api_get_product(product_id)
            return {"data": channel._api_product_payload(product, channel._api_available_qty(product)[product.id])}

        return self._dispatch("catalog:read", handler)

    @http.route(f"{PREFIX}/products/<int:product_id>/image", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def product_image(self, product_id, size=1024, **kwargs):
        def handler(channel):
            product = channel._api_get_product(product_id)
            field = {"128": "image_128", "256": "image_256", "512": "image_512"}.get(str(size), "image_1024")
            if not product[field]:
                raise ApiError(404, "not_found", "This product has no image.")
            stream = request.env["ir.binary"]._get_image_stream_from(product.sudo(), field)
            return stream.get_response(max_age=3600)

        return self._dispatch("catalog:read", handler)

    @http.route(f"{PREFIX}/stock", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def stock(self, **params):
        def handler(channel):
            page, size = page_params(params)
            domain = channel._api_product_domain() & Domain("active", "=", True)
            if params.get("skus"):
                domain &= Domain("default_code", "in", params["skus"].split(","))
            if params.get("product_ids"):
                domain &= Domain("id", "in", [int(i) for i in params["product_ids"].split(",") if i.strip().isdigit()])
            warehouse = None
            if params.get("location_id"):
                warehouse = (channel.warehouse_id | channel.pickup_warehouse_ids).filtered(
                    lambda w: str(w.id) == params["location_id"] or w.code == params["location_id"]
                )[:1]
                if not warehouse:
                    raise ApiError(404, "not_found", "Unknown location_id.")
            Product = channel.env["product.product"]
            total = Product.search_count(domain)
            products = Product.search(domain, order="id", limit=size, offset=(page - 1) * size)
            warehouse = warehouse or channel.warehouse_id
            return {
                "data": channel._api_stock_levels(products, warehouse),
                "meta": {**page_meta(page, size, total), "location": channel._api_warehouse_payload(warehouse)},
            }

        return self._dispatch("stock:read", handler)

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------
    @http.route(f"{PREFIX}/customers", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def customer_upsert(self, **kwargs):
        def handler(channel):
            partner = channel._api_find_or_create_customer(self._body())
            return {"data": channel._api_customer_payload(partner)}

        return self._dispatch("customers:write", handler)

    @http.route(f"{PREFIX}/customers/<int:customer_id>", type="http", auth="public", methods=["GET"], csrf=False,
                save_session=False)
    def customer(self, customer_id, **kwargs):
        def handler(channel):
            partner = channel.env["res.partner"].search(
                [("id", "=", customer_id), ("company_id", "in", (False, channel.company_id.id))], limit=1
            )
            if not partner:
                raise ApiError(404, "not_found", f"Customer {customer_id} not found.")
            return {"data": channel._api_customer_payload(partner)}

        return self._dispatch("customers:read", handler)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    @http.route(f"{PREFIX}/orders", type="http", auth="public", methods=["POST"], csrf=False, save_session=False)
    def order_create(self, **kwargs):
        def handler(channel):
            order, created, warnings = channel._api_create_order(self._body())
            payload = {"data": channel._api_order_payload(order), "warnings": warnings}
            if not created:
                payload["replayed"] = True
            return payload, 201 if created else 200

        return self._dispatch("orders:write", handler)

    @http.route(f"{PREFIX}/orders", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def order_list(self, **params):
        def handler(channel):
            page, size = page_params(params)
            domain = Domain("ke_channel_id", "=", channel.id)
            if params.get("updated_since"):
                domain &= Domain("write_date", ">", parse_datetime(params["updated_since"], "updated_since"))
            status = params.get("status")
            if status:
                state = {"pending": ["draft", "sent"], "cancelled": ["cancel"]}.get(status, ["sale"])
                domain &= Domain("state", "in", state)
            if params.get("payment_status"):
                domain &= Domain("ke_payment_state", "=", params["payment_status"])
            Order = channel.env["sale.order"]
            total = Order.search_count(domain)
            orders = Order.search(domain, order="id desc", limit=size, offset=(page - 1) * size)
            return {"data": [channel._api_order_payload(o) for o in orders], "meta": page_meta(page, size, total)}

        return self._dispatch("orders:read", handler)

    @http.route(f"{PREFIX}/orders/<string:ref>", type="http", auth="public", methods=["GET"], csrf=False,
                save_session=False)
    def order_get(self, ref, **kwargs):
        return self._dispatch(
            "orders:read", lambda channel: {"data": channel._api_order_payload(channel._api_find_order(ref))}
        )

    @http.route(f"{PREFIX}/orders/<string:ref>/cancel", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def order_cancel(self, ref, **kwargs):
        def handler(channel):
            order = channel._api_cancel_order(channel._api_find_order(ref), (self._body() or {}).get("reason"))
            return {"data": channel._api_order_payload(order)}

        return self._dispatch("orders:write", handler)

    @http.route(f"{PREFIX}/orders/<string:ref>/payments", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def order_payment(self, ref, **kwargs):
        def handler(channel):
            order = channel._api_find_order(ref)
            payment, created = channel._api_register_payment(order, self._body())
            order.invalidate_recordset()
            payload = {"data": {"payment": channel._api_payment_payload(payment),
                                "order": channel._api_order_payload(order)}}
            if not created:
                payload["replayed"] = True
            return payload, 201 if created else 200

        return self._dispatch("payments:write", handler)

    @http.route(f"{PREFIX}/orders/<string:ref>/mpesa-stk-push", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def order_stk_push(self, ref, **kwargs):
        def handler(channel):
            order = channel._api_find_order(ref)
            tx = channel._api_stk_push(order, self._body())
            return {"data": tx._ke_api_status_payload(tx)}, 202

        return self._dispatch("payments:write", handler)

    @http.route(f"{PREFIX}/mpesa/stk-requests/<int:tx_id>", type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False)
    def stk_status(self, tx_id, **kwargs):
        def handler(channel):
            tx = channel.env["ke.mpesa.transaction"].sudo().search(
                [("id", "=", tx_id), ("ke_sale_order_id.ke_channel_id", "=", channel.id)], limit=1
            )
            if not tx:
                raise ApiError(404, "not_found", "Unknown STK request.")
            if tx.state == "pending":
                tx._query_stk_status(min_interval=10)
            return {"data": tx._ke_api_status_payload(tx)}

        return self._dispatch("orders:read", handler)

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------
    @http.route(f"{PREFIX}/webhooks/events", type="http", auth="public", methods=["GET"], csrf=False,
                save_session=False)
    def webhook_events(self, **params):
        def handler(channel):
            page, size = page_params(params)
            domain = [("channel_id", "=", channel.id)]
            if params.get("state"):
                domain.append(("state", "=", params["state"]))
            Event = channel.env["ke.ecommerce.webhook.event"].sudo()
            total = Event.search_count(domain)
            events = Event.search(domain, limit=size, offset=(page - 1) * size)
            return {
                "data": [
                    {
                        "id": e.uid,
                        "type": e.topic,
                        "state": e.state,
                        "attempts": e.attempts,
                        "last_status_code": e.last_status_code or None,
                        "last_error": e.last_error or None,
                        "created_at": format_datetime(e.create_date),
                        "sent_at": format_datetime(e.sent_at),
                    }
                    for e in events
                ],
                "meta": page_meta(page, size, total),
            }

        return self._dispatch("webhooks:read", handler)

    @http.route(f"{PREFIX}/webhooks/test", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def webhook_test(self, **kwargs):
        def handler(channel):
            event = channel.env["ke.ecommerce.webhook.event"].sudo()._enqueue(
                channel, "ping", {"message": "Webhook test from Odoo"}, force=True
            )
            return {"data": {"id": event.uid, "type": "ping", "state": event.state}}, 202

        return self._dispatch("webhooks:write", handler)

    @http.route(f"{PREFIX}/webhooks/events/<string:uid>/redeliver", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def webhook_redeliver(self, uid, **kwargs):
        def handler(channel):
            event = channel.env["ke.ecommerce.webhook.event"].sudo().search(
                [("uid", "=", uid), ("channel_id", "=", channel.id)], limit=1
            )
            if not event:
                raise ApiError(404, "not_found", "Unknown event.")
            event.write({"state": "pending", "attempts": 0, "next_attempt_at": fields.Datetime.now()})
            return {"data": {"id": event.uid, "state": event.state}}, 202

        return self._dispatch("webhooks:write", handler)
