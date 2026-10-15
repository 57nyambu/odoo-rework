import json

from odoo import Command
from odoo.tests import HttpCase

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class KeEcommerceApiCommon(AccountTestInvoicingCommon, HttpCase):
    """A Kenyan company with a channel, published products, stock and
    API keys; helpers to call the API over real HTTP."""

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("ke_ecommerce_api.group_ecommerce_manager")
        cls.company = cls.company_data["company"]
        cls.company.write(
            {
                "vat": "P051234567X",
                "ke_etims_enabled": True,
                "ke_etims_connector": "demo",
                "ke_etims_environment": "sandbox",
            }
        )
        ChartTemplate = cls.env["account.chart.template"].with_company(cls.company)
        cls.vat16 = ChartTemplate.ref("ST16")
        cls.vat_exempt = ChartTemplate.ref("STEX")
        # Kenyan retail prices are VAT inclusive
        (cls.vat16 | cls.vat_exempt).write({"price_include_override": "tax_included"})

        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.company.id)], limit=1)
        cls.branch = cls.env["stock.warehouse"].create(
            {"name": "Westlands branch", "code": "WSTL", "company_id": cls.company.id}
        )
        cls.branch.partner_id.write({"city": "Nairobi", "state_id": cls.env.ref("base.state_ke_30").id})

        Product = cls.env["product.product"].with_company(cls.company)
        cls.soda = Product.create(
            {
                "name": "Soda 500ml",
                "default_code": "SODA-500",
                "barcode": "6161234500017",
                "list_price": 116.0,
                "is_storable": True,
                "taxes_id": [Command.set(cls.vat16.ids)],
                "company_id": cls.company.id,
            }
        )
        cls.unga = Product.create(
            {
                "name": "Unga 2kg",
                "default_code": "UNGA-2KG",
                "list_price": 180.0,
                "is_storable": True,
                "taxes_id": [Command.set(cls.vat_exempt.ids)],
                "company_id": cls.company.id,
            }
        )
        cls.hidden = Product.create(
            {"name": "Not online", "default_code": "HIDDEN", "list_price": 10.0, "company_id": cls.company.id}
        )
        for product, qty, warehouse in ((cls.soda, 10, cls.warehouse), (cls.unga, 5, cls.warehouse), (cls.soda, 3, cls.branch)):
            cls.env["stock.quant"].with_company(cls.company).with_context(inventory_mode=True).create(
                {"product_id": product.id, "location_id": warehouse.lot_stock_id.id, "inventory_quantity": qty}
            ).action_apply_inventory()

        cls.channel = cls.env["ke.ecommerce.channel"].with_company(cls.company).create(
            {
                "name": "Duka Online",
                "code": "duka-online",
                "company_id": cls.company.id,
                "warehouse_id": cls.warehouse.id,
                "pickup_warehouse_ids": [Command.set(cls.branch.ids)],
                "pricelist_id": cls.env["product.pricelist"].create(
                    {"name": "Online KES", "currency_id": cls.company.currency_id.id, "company_id": cls.company.id}
                ).id,
                "webhook_url": "https://shop.example.test/odoo-webhooks",
                "payment_journal_id": cls.company_data["default_journal_bank"].id,
            }
        )
        (cls.soda | cls.unga).product_tmpl_id.write({"ke_ecommerce_channel_ids": [Command.link(cls.channel.id)]})
        all_scopes = cls.env["ke.ecommerce.api.scope"].search([])
        cls.key_record, cls.key = cls.env["ke.ecommerce.api.key"]._generate(cls.channel, "tests", all_scopes)
        _rec, cls.catalog_key = cls.env["ke.ecommerce.api.key"]._generate(
            cls.channel, "catalog only", cls.env.ref("ke_ecommerce_api.scope_catalog_read")
        )

    def call(self, method, path, body=None, key=None, headers=None):
        all_headers = {"Authorization": f"Bearer {key or self.key}"}
        if body is not None:
            all_headers["Content-Type"] = "application/json"
        all_headers.update(headers or {})
        response = self.url_open(
            f"/api/ke/v1{path}",
            data=json.dumps(body) if body is not None else None,
            headers=all_headers,
            method=method,
            timeout=60,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response, payload

    def order_payload(self, external_id="WC-1001", **overrides):
        payload = {
            "external_id": external_id,
            "currency": "KES",
            "prices_include_tax": True,
            "placed_at": "2026-10-15T10:15:00+03:00",
            "customer": {
                "external_id": "cust-77",
                "name": "Achieng Otieno",
                "email": "achieng@example.co.ke",
                "phone": "0712 345 678",
                "kra_pin": "A012345678Z",
            },
            "shipping_address": {
                "name": "Achieng Otieno",
                "phone": "0712345678",
                "street": "Ngong Road, Prestige Plaza",
                "city": "Nairobi",
                "county": "Nairobi",
            },
            "fulfilment": {"type": "delivery", "shipping_fee": 200, "carrier": "Sendy"},
            "lines": [
                {"sku": "SODA-500", "quantity": 2, "unit_price": 116},
                {"sku": "UNGA-2KG", "quantity": 1, "unit_price": 180},
            ],
        }
        payload.update(overrides)
        return payload
