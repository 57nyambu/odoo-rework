import re
import secrets

from odoo import api, fields, models
from odoo.exceptions import ValidationError

API_USER_GROUPS = (
    "base.group_user",
    "base.group_partner_manager",
    "sales_team.group_sale_salesman_all_leads",
    "stock.group_stock_user",
    "account.group_account_invoice",
    "ke_ecommerce_api.group_ecommerce_api_user",
)


class KeEcommerceChannel(models.Model):
    """An external online shop (WooCommerce, Shopify, a custom Laravel /
    Django / Node store, a marketplace...) exchanging data with Odoo.

    Every API call is authenticated with an API key bound to one channel and
    runs as the channel's technical user, restricted to the channel's
    company: one Odoo company never sees another company's data through the
    API (multi-company tenancy), and each tenant database has its own
    channels (database-per-tenant).
    """

    _name = "ke.ecommerce.channel"
    _description = "E-commerce channel"
    _order = "company_id, name"
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        help="Short identifier (letters, digits, dashes), e.g. 'duka-online'. Used in "
        "order references and the technical user login.",
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    user_id = fields.Many2one(
        "res.users",
        string="API user",
        readonly=True,
        copy=False,
        help="Technical user the API acts as. Created automatically with the minimum "
        "rights needed (sales, inventory, billing) in this company only.",
    )

    # --- catalog & stock --------------------------------------------------
    publish_all_products = fields.Boolean(
        default=False,
        help="Expose every saleable product. Otherwise only products where this "
        "channel is ticked in 'Online channels' are exposed.",
    )
    pricelist_id = fields.Many2one(
        "product.pricelist",
        required=True,
        check_company=True,
        default=lambda self: self.env["product.pricelist"].search(
            [("company_id", "in", (False, self.env.company.id))], limit=1
        ),
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Ships from",
        required=True,
        check_company=True,
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1),
    )
    pickup_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        string="Pickup branches",
        check_company=True,
        help="Branches where online customers can collect their order (click & collect).",
    )
    stock_buffer = fields.Float(
        default=0.0,
        help="Units held back from online availability for every product, to avoid "
        "overselling items that also sell in the shops (POS).",
    )
    allow_backorder = fields.Boolean(
        help="Accept online orders for more units than available.",
    )

    # --- orders -----------------------------------------------------------
    price_source = fields.Selection(
        [("odoo", "Odoo pricelist"), ("platform", "Prices sent by the platform")],
        default="odoo",
        required=True,
        help="Odoo pricelist: the platform's prices are checked and Odoo's are used. "
        "Platform: the order is booked at the prices the customer saw online.",
    )
    team_id = fields.Many2one("crm.team", string="Sales team", check_company=True)
    shipping_product_id = fields.Many2one(
        "product.product",
        string="Delivery fee product",
        check_company=True,
        domain="[('type', '=', 'service')]",
    )
    auto_confirm = fields.Boolean(default=True, help="Confirm orders (and reserve stock) as soon as they arrive.")
    auto_invoice = fields.Boolean(
        default=True,
        help="Invoice the order as soon as it is fully paid, which transmits it to "
        "KRA eTIMS when eTIMS is enabled.",
    )

    # --- payments ---------------------------------------------------------
    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Online payments journal",
        check_company=True,
        domain="[('type', '=', 'bank')]",
        help="Bank journal where card / bank / other online payments are recorded.",
    )
    mpesa_journal_id = fields.Many2one(
        "account.journal",
        string="M-Pesa journal",
        check_company=True,
        domain="[('type', '=', 'bank')]",
    )
    mpesa_account_id = fields.Many2one(
        "ke.mpesa.account",
        string="M-Pesa account",
        check_company=True,
        help="Till / paybill used when the platform asks Odoo to send an STK push.",
    )
    mpesa_verification = fields.Selection(
        [
            ("none", "Trust the platform"),
            ("if_available", "Check against Safaricom notifications when available"),
            ("required", "Require a matching Safaricom notification"),
        ],
        default="none",
        required=True,
        help="How M-Pesa codes reported by the platform are verified before being "
        "booked. Checks need the C2B URLs of the M-Pesa account to be registered.",
    )

    # --- webhooks ---------------------------------------------------------
    webhook_url = fields.Char(help="HTTPS endpoint of the platform receiving event notifications.")
    webhook_secret = fields.Char(
        copy=False,
        groups="ke_ecommerce_api.group_ecommerce_manager",
        default=lambda self: secrets.token_hex(32),
        help="Used to sign webhook bodies (HMAC-SHA256). Share it with the platform.",
    )
    webhook_topic_ids = fields.Many2many("ke.ecommerce.webhook.topic", string="Events sent")

    # --- limits -----------------------------------------------------------
    rate_limit_per_minute = fields.Integer(default=120)

    api_key_ids = fields.One2many("ke.ecommerce.api.key", "channel_id", string="API keys")
    order_ids = fields.One2many("sale.order", "ke_channel_id")
    order_count = fields.Integer(compute="_compute_order_count")
    last_stock_sync_at = fields.Datetime(readonly=True, copy=False)

    _code_uniq = models.Constraint("unique(company_id, code)", "The channel code must be unique per company.")

    @api.constrains("code")
    def _check_code(self):
        for channel in self:
            if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{1,30}", channel.code or ""):
                raise ValidationError(
                    self.env._("The code uses 2-31 lowercase letters, digits or dashes, e.g. duka-online.")
                )

    @api.constrains("webhook_url")
    def _check_webhook_url(self):
        for channel in self:
            if channel.webhook_url and not channel.webhook_url.startswith(("https://", "http://")):
                raise ValidationError(self.env._("The webhook URL must start with https://"))

    def _compute_order_count(self):
        counts = dict(
            self.env["sale.order"]._read_group([("ke_channel_id", "in", self.ids)], ["ke_channel_id"], ["__count"])
        )
        for channel in self:
            channel.order_count = counts.get(channel, 0)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        channels = super().create(vals_list)
        for channel in channels:
            channel._setup_defaults()
        return channels

    def write(self, vals):
        res = super().write(vals)
        if "company_id" in vals or "active" in vals:
            for channel in self:
                channel._sync_api_user()
        return res

    def _setup_defaults(self):
        self.ensure_one()
        sudo = self.sudo()
        vals = {}
        if not sudo.user_id:
            vals["user_id"] = self._create_api_user().id
        if not sudo.team_id:
            vals["team_id"] = (
                self.env["crm.team"].sudo().create(
                    {"name": self.env._("Online - %s", self.name), "company_id": self.company_id.id}
                ).id
            )
        if not sudo.shipping_product_id:
            vals["shipping_product_id"] = self._create_shipping_product().id
        if not sudo.payment_journal_id:
            vals["payment_journal_id"] = (
                self.env["account.journal"].sudo().search(
                    [("type", "=", "bank"), ("company_id", "=", self.company_id.id)], limit=1
                ).id
            )
        if not sudo.webhook_topic_ids:
            vals["webhook_topic_ids"] = [(6, 0, self.env["ke.ecommerce.webhook.topic"].sudo().search([]).ids)]
        if vals:
            sudo.write(vals)

    def _create_api_user(self):
        groups = self.env["res.groups"]
        for xmlid in API_USER_GROUPS:
            groups |= self.env.ref(xmlid)
        return (
            self.env["res.users"]
            .sudo()
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": self.env._("%s (API)", self.name),
                    "login": f"api+{self.code}.{self.company_id.id}@ke-ecommerce.invalid",
                    "company_id": self.company_id.id,
                    "company_ids": [(6, 0, self.company_id.ids)],
                    "group_ids": [(6, 0, groups.ids)],
                    "tz": "Africa/Nairobi",
                }
            )
        )

    def _sync_api_user(self):
        user = self.sudo().user_id
        if user:
            user.write(
                {
                    "active": self.active,
                    "company_id": self.company_id.id,
                    "company_ids": [(6, 0, self.company_id.ids)],
                }
            )

    def _create_shipping_product(self):
        Tax = self.env["account.tax"].sudo()
        vat = Tax.search(
            [
                ("company_id", "=", self.company_id.id),
                ("type_tax_use", "=", "sale"),
                ("amount_type", "=", "percent"),
                ("amount", "=", 16.0),
            ],
            limit=1,
        )
        return self.env["product.product"].sudo().create(
            {
                "name": self.env._("Online delivery fee"),
                "type": "service",
                "sale_ok": True,
                "purchase_ok": False,
                "list_price": 0.0,
                "invoice_policy": "order",
                "company_id": self.company_id.id,
                "taxes_id": [(6, 0, vat.ids)],
                "default_code": f"DELIVERY-{self.code.upper()}",
            }
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_new_api_key(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("New API key"),
            "res_model": "ke.ecommerce.api.key.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_channel_id": self.id},
        }

    def action_regenerate_webhook_secret(self):
        for channel in self.sudo():
            channel.webhook_secret = secrets.token_hex(32)
        return True

    def action_send_test_webhook(self):
        self.ensure_one()
        self.env["ke.ecommerce.webhook.event"].sudo()._enqueue(
            self, "ping", {"message": "Webhook test from Odoo", "channel": self.code}, force=True
        )._dispatch()
        return self.action_view_webhook_events()

    def action_view_orders(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("sale.action_orders")
        action.update({"domain": [("ke_channel_id", "=", self.id)], "context": {"default_ke_channel_id": self.id}})
        return action

    def action_view_webhook_events(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("ke_ecommerce_api.action_ke_ecommerce_webhook_event")
        action["domain"] = [("channel_id", "=", self.id)]
        return action

    def action_view_request_logs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("ke_ecommerce_api.action_ke_ecommerce_request_log")
        action["domain"] = [("channel_id", "=", self.id)]
        return action

    # ------------------------------------------------------------------
    # Helpers used by the API
    # ------------------------------------------------------------------
    def _api_env(self):
        """Environment the API works in: the technical user, this company only."""
        self.ensure_one()
        user = self.sudo().user_id
        return self.with_user(user).with_context(
            allowed_company_ids=self.company_id.ids,
            tz="Africa/Nairobi",
            lang=user.lang or "en_US",
            tracking_disable=False,
        ).with_company(self.company_id)

    def _subscribed(self, topic):
        self.ensure_one()
        return topic == "ping" or topic in self.sudo().webhook_topic_ids.mapped("code")
