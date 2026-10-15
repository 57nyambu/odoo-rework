import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import timedelta

import requests

from odoo import api, fields, models

from .api_tools import API_VERSION, format_datetime

_logger = logging.getLogger(__name__)

# Delay before attempt n+1 (minutes). After the last one the event fails.
RETRY_SCHEDULE = [1, 5, 15, 60, 180, 360, 720, 1440]
TIMEOUT = (5, 15)
BATCH_SIZE = 50


def sign_payload(secret, timestamp, body):
    """``v1`` signature: hex HMAC-SHA256 of ``"<timestamp>.<raw body>"``."""
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class KeEcommerceWebhookTopic(models.Model):
    _name = "ke.ecommerce.webhook.topic"
    _description = "E-commerce webhook topic"
    _order = "code"

    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)

    _code_uniq = models.Constraint("unique(code)", "Topic codes must be unique.")

    @api.depends("code")
    def _compute_display_name(self):
        for topic in self:
            topic.display_name = topic.code


class KeEcommerceWebhookEvent(models.Model):
    """Outbox of notifications to a channel's webhook URL.

    Events are written in the same transaction as the change they describe
    and delivered afterwards by a cron, so a notification is never sent for
    a change that was rolled back, and a platform outage only delays them.
    """

    _name = "ke.ecommerce.webhook.event"
    _description = "E-commerce webhook event"
    _order = "id desc"

    uid = fields.Char(string="Event ID", required=True, index=True, readonly=True, copy=False,
                      default=lambda self: f"evt_{uuid.uuid4().hex}")
    channel_id = fields.Many2one("ke.ecommerce.channel", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="channel_id.company_id", store=True, index=True)
    topic = fields.Char(required=True, index=True)
    res_model = fields.Char(index=True)
    res_id = fields.Integer(index=True)
    payload = fields.Text(required=True)
    state = fields.Selection(
        [("pending", "Pending"), ("sent", "Delivered"), ("failed", "Failed"), ("cancelled", "Cancelled")],
        default="pending",
        required=True,
        index=True,
    )
    attempts = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True)
    sent_at = fields.Datetime(readonly=True)
    last_status_code = fields.Integer(readonly=True)
    last_error = fields.Text(readonly=True)

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------
    @api.model
    def _enqueue(self, channel, topic, data, record=None, debounce=False, force=False):
        """Queue an event for ``channel`` if it subscribed to ``topic``.

        With ``debounce``, a still pending event for the same record and
        topic is updated instead of queuing a second one (product and stock
        updates).
        """
        if not channel or not channel.active:
            return self.browse()
        if not force and (not channel.webhook_url or not channel._subscribed(topic)):
            return self.browse()
        Event = self.sudo()
        res_model = record._name if record else False
        res_id = record.id if record else 0
        if debounce and record:
            pending = Event.search(
                [
                    ("channel_id", "=", channel.id),
                    ("topic", "=", topic),
                    ("res_model", "=", res_model),
                    ("res_id", "=", res_id),
                    ("state", "=", "pending"),
                    ("attempts", "=", 0),
                ],
                limit=1,
            )
            if pending:
                pending.payload = json.dumps(data, default=str)
                return pending
        event = Event.create(
            {
                "channel_id": channel.id,
                "topic": topic,
                "res_model": res_model,
                "res_id": res_id,
                "payload": json.dumps(data, default=str),
            }
        )
        cron = self.env.ref("ke_ecommerce_api.ir_cron_ke_ecommerce_webhooks", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return event

    def _envelope(self):
        self.ensure_one()
        return {
            "id": self.uid,
            "type": self.topic,
            "created_at": format_datetime(self.create_date),
            "api_version": API_VERSION,
            "channel": self.channel_id.code,
            "data": json.loads(self.payload),
        }

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    def _dispatch(self):
        """Deliver events. Returns the delivered ones."""
        delivered = self.browse()
        for event in self.sudo():
            channel = event.channel_id
            if event.state != "pending" or not channel.webhook_url:
                continue
            body = json.dumps(event._envelope(), separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Odoo-Kenya-Webhooks/1.0",
                "X-KE-Event": event.topic,
                "X-KE-Event-Id": event.uid,
                "X-KE-Delivery-Attempt": str(event.attempts + 1),
                "X-KE-Signature": f"t={timestamp},v1={sign_payload(channel.webhook_secret or '', timestamp, body)}",
            }
            status = 0
            error = False
            try:
                response = requests.post(channel.webhook_url, data=body, headers=headers, timeout=TIMEOUT)
                status = response.status_code
                if not 200 <= status < 300:
                    error = f"HTTP {status}: {response.text[:500]}"
            except requests.RequestException as exc:
                error = f"{exc.__class__.__name__}: {exc}"
            attempts = event.attempts + 1
            vals = {"attempts": attempts, "last_status_code": status, "last_error": error}
            if not error:
                vals.update({"state": "sent", "sent_at": fields.Datetime.now()})
                delivered |= event
            elif attempts > len(RETRY_SCHEDULE):
                vals["state"] = "failed"
            else:
                vals["next_attempt_at"] = fields.Datetime.now() + timedelta(minutes=RETRY_SCHEDULE[attempts - 1])
            event.write(vals)
        return delivered

    @api.model
    def _cron_dispatch(self):
        while True:
            events = self.sudo().search(
                [("state", "=", "pending"), ("next_attempt_at", "<=", fields.Datetime.now())],
                order="id",
                limit=BATCH_SIZE,
            )
            if not events:
                return
            events._dispatch()
            if not self.env["ir.cron"]._commit_progress(len(events)):
                return

    @api.model
    def _cron_purge(self, days=30):
        self.sudo().search(
            [("state", "in", ("sent", "cancelled")), ("create_date", "<", fields.Datetime.now() - timedelta(days=days))]
        ).unlink()

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------
    def action_redeliver(self):
        self.write({"state": "pending", "next_attempt_at": fields.Datetime.now(), "attempts": 0})
        self._dispatch()
        return True

    def action_cancel(self):
        self.filtered(lambda e: e.state in ("pending", "failed")).write({"state": "cancelled"})
        return True
