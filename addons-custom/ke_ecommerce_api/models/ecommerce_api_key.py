import hashlib
import hmac
import secrets
from datetime import timedelta

from odoo import api, fields, models

KEY_PREFIX = "ke"


def _hash_secret(secret):
    # API secrets are 256-bit random values: a plain SHA-256 is enough (no
    # need for a slow password hash) and keeps authentication cheap.
    return hashlib.sha256(secret.encode()).hexdigest()


class KeEcommerceApiScope(models.Model):
    _name = "ke.ecommerce.api.scope"
    _description = "E-commerce API scope"
    _order = "code"

    code = fields.Char(required=True)
    name = fields.Char(required=True, translate=True)

    _code_uniq = models.Constraint("unique(code)", "Scope codes must be unique.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for scope in self:
            scope.display_name = scope.code


class KeEcommerceApiKey(models.Model):
    """API key of a channel. Only a hash of the secret is stored; the full
    key is displayed once, when it is generated."""

    _name = "ke.ecommerce.api.key"
    _description = "E-commerce API key"
    _order = "create_date desc"

    name = fields.Char(required=True, help="Where the key is used, e.g. 'Production web server'.")
    channel_id = fields.Many2one("ke.ecommerce.channel", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="channel_id.company_id", store=True, index=True)
    key_prefix = fields.Char(readonly=True, index=True, copy=False)
    key_hash = fields.Char(readonly=True, copy=False, groups="base.group_system")
    scope_ids = fields.Many2many("ke.ecommerce.api.scope", string="Scopes")
    expires_at = fields.Datetime(help="Leave empty for a key that does not expire.")
    active = fields.Boolean(default=True)
    last_used_at = fields.Datetime(readonly=True, copy=False)
    last_used_ip = fields.Char(readonly=True, copy=False)

    _prefix_uniq = models.Constraint("unique(key_prefix)", "API key prefix collision, generate the key again.")

    @api.model
    def _generate(self, channel, name, scopes, expires_at=False):
        """Create a key and return ``(record, plaintext_key)``."""
        prefix = secrets.token_hex(5)
        secret = secrets.token_urlsafe(32)
        record = self.sudo().create(
            {
                "name": name,
                "channel_id": channel.id,
                "key_prefix": prefix,
                "key_hash": _hash_secret(secret),
                "scope_ids": [(6, 0, scopes.ids)],
                "expires_at": expires_at,
            }
        )
        return record, f"{KEY_PREFIX}_{prefix}_{secret}"

    @api.model
    def _authenticate(self, plaintext):
        """Return the active key matching ``plaintext`` or an empty recordset."""
        try:
            marker, prefix, secret = (plaintext or "").split("_", 2)
        except ValueError:
            return self.browse()
        if marker != KEY_PREFIX or not prefix or not secret:
            return self.browse()
        key = self.sudo().search([("key_prefix", "=", prefix)], limit=1)
        if not key or not hmac.compare_digest(key.key_hash or "", _hash_secret(secret)):
            return self.browse()
        if key.expires_at and key.expires_at < fields.Datetime.now():
            return self.browse()
        if not key.channel_id.active:
            return self.browse()
        return key

    def _has_scope(self, scope):
        self.ensure_one()
        return scope in self.sudo().scope_ids.mapped("code")

    def _touch(self, ip_address):
        """Record usage, at most once a minute to avoid write contention."""
        self.ensure_one()
        now = fields.Datetime.now()
        if not self.last_used_at or self.last_used_at < now - timedelta(minutes=1):
            self.sudo().write({"last_used_at": now, "last_used_ip": ip_address})

    def action_revoke(self):
        self.write({"active": False})
        return True
