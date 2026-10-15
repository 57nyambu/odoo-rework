import ipaddress
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class KeMpesaCallbackController(http.Controller):
    """Endpoints Safaricom calls back.

    URLs deliberately avoid the words Daraja refuses in callback URLs
    ("mpesa", "safaricom", "query", "exe", "cmd", "sql"...). Each URL ends
    with the account's secret token: Daraja does not sign callbacks, so the
    unguessable URL (plus the optional IP allow-list) authenticates them.

    In a multi-tenant deployment the tenant database is selected from the
    host name (dbfilter), so each tenant registers its own URLs.
    """

    def _json(self, data, status=200):
        return request.make_json_response(data, status=status)

    def _payload(self):
        try:
            return json.loads(request.httprequest.get_data() or b"{}")
        except (TypeError, ValueError):
            return None

    def _client_allowed(self):
        allowed = request.env["ir.config_parameter"].sudo().get_param("ke_mpesa.allowed_callback_ips")
        if not allowed:
            return True
        remote = request.httprequest.remote_addr
        try:
            address = ipaddress.ip_address(remote)
        except ValueError:
            return False
        for entry in allowed.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        _logger.warning("M-Pesa callback from non allow-listed address %s refused", remote)
        return False

    def _account(self, token):
        if not token or not self._client_allowed():
            return None
        account = request.env["ke.mpesa.account"].sudo().with_context(active_test=False).search(
            [("callback_token", "=", token)], limit=1
        )
        return account or None

    @http.route("/ke/lipa/stk/<string:token>", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def stk_callback(self, token, **kwargs):
        account = self._account(token)
        if not account:
            return self._json({"ResultCode": 1, "ResultDesc": "Rejected"}, status=403)
        payload = self._payload()
        if payload is None:
            return self._json({"ResultCode": 1, "ResultDesc": "Invalid payload"}, status=400)
        request.env["ke.mpesa.transaction"].sudo().with_company(account.company_id)._process_stk_callback(
            account, payload
        )
        return self._json({"ResultCode": 0, "ResultDesc": "Accepted"})

    @http.route("/ke/lipa/c2b/validate/<string:token>", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def c2b_validate(self, token, **kwargs):
        account = self._account(token)
        if not account:
            return self._json({"ResultCode": "C2B00016", "ResultDesc": "Rejected"}, status=403)
        payload = self._payload() or {}
        accepted, code = request.env["ke.mpesa.transaction"].sudo()._c2b_validate(account, payload)
        return self._json({"ResultCode": code, "ResultDesc": "Accepted" if accepted else "Rejected"})

    @http.route("/ke/lipa/c2b/confirm/<string:token>", type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False)
    def c2b_confirm(self, token, **kwargs):
        account = self._account(token)
        if not account:
            return self._json({"ResultCode": 1, "ResultDesc": "Rejected"}, status=403)
        payload = self._payload()
        if payload is None:
            return self._json({"ResultCode": 1, "ResultDesc": "Invalid payload"}, status=400)
        request.env["ke.mpesa.transaction"].sudo().with_company(account.company_id)._process_c2b_confirmation(
            account, payload
        )
        return self._json({"ResultCode": 0, "ResultDesc": "Accepted"})
