from odoo import fields, models
from odoo.fields import Domain
from odoo.tools import float_round

from .api_tools import ApiError, format_datetime, parse_datetime


class KeEcommerceChannel(models.Model):
    _inherit = "ke.ecommerce.channel"

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------
    def _api_product_domain(self):
        self.ensure_one()
        domain = Domain("sale_ok", "=", True) & Domain("company_id", "in", (False, self.company_id.id))
        # delivery-fee products of the channels are never sold as items
        shipping = self.sudo().search([("company_id", "=", self.company_id.id)]).shipping_product_id
        if shipping:
            domain &= Domain("id", "not in", shipping.ids)
        if not self.publish_all_products:
            domain &= Domain("product_tmpl_id.ke_ecommerce_channel_ids", "in", self.ids)
        return domain

    def _api_search_products(self, params, page, size):
        self.ensure_one()
        Product = self.env["product.product"].with_context(active_test=False)
        domain = self._api_product_domain()
        if not params.get("include_archived"):
            domain &= Domain("active", "=", True)
        if params.get("updated_since"):
            since = parse_datetime(params["updated_since"], "updated_since")
            domain &= Domain("write_date", ">", since) | Domain("product_tmpl_id.write_date", ">", since)
        if params.get("sku"):
            domain &= Domain("default_code", "in", str(params["sku"]).split(","))
        if params.get("barcode"):
            domain &= Domain("barcode", "in", str(params["barcode"]).split(","))
        if params.get("category_id"):
            domain &= Domain("categ_id", "child_of", int(params["category_id"]))
        if params.get("q"):
            domain &= Domain("name", "ilike", params["q"]) | Domain("default_code", "ilike", params["q"])
        total = Product.search_count(domain)
        products = Product.search(domain, order="id", limit=size, offset=(page - 1) * size)
        return products, total

    def _api_get_product(self, product_id):
        self.ensure_one()
        product = (
            self.env["product.product"]
            .with_context(active_test=False)
            .search(self._api_product_domain() & Domain("id", "=", int(product_id)), limit=1)
        )
        if not product:
            raise ApiError(404, "not_found", f"Product {product_id} is not published on this channel.")
        return product

    def _api_available_qty(self, products, warehouse=None):
        """Units the platform may sell: free quantity (on hand minus reserved)
        in the channel warehouse, minus the safety buffer. Services and
        non-tracked goods are always available (None)."""
        self.ensure_one()
        warehouse = warehouse or self.warehouse_id
        storable = products.filtered("is_storable")
        quantities = {}
        if storable:
            rows = storable.with_context(warehouse_id=warehouse.id).read(["free_qty"])
            quantities = {row["id"]: row["free_qty"] for row in rows}
        result = {}
        for product in products:
            if product.id in quantities:
                qty = max(quantities[product.id] - (self.stock_buffer or 0.0), 0.0)
                result[product.id] = float_round(qty, precision_rounding=product.uom_id.rounding)
            else:
                result[product.id] = None
        return result

    def _api_prices(self, product, quantity=1.0):
        """Unit price of ``product`` on the channel pricelist, both excluding
        and including taxes, as the online shop should display it."""
        self.ensure_one()
        pricelist = self.pricelist_id
        price = pricelist._get_product_price(product, quantity, currency=pricelist.currency_id)
        taxes = product.taxes_id._filter_taxes_by_company(self.company_id)
        computed = taxes.compute_all(price, currency=pricelist.currency_id, quantity=1.0, product=product)
        return {
            "currency": pricelist.currency_id.name,
            "price": computed["total_included"],
            "price_excluding_tax": computed["total_excluded"],
            "tax_amount": computed["total_included"] - computed["total_excluded"],
        }

    def _api_product_payload(self, product, available=None):
        self.ensure_one()
        template = product.product_tmpl_id
        taxes = product.taxes_id._filter_taxes_by_company(self.company_id)
        Tax = self.env["account.tax"]
        tax_type = Tax._ke_etims_tax_type_for(taxes) if hasattr(Tax, "_ke_etims_tax_type_for") else None
        prices = self._api_prices(product)
        variant_values = [
            {"attribute": value.attribute_id.name, "value": value.name}
            for value in product.product_template_attribute_value_ids.product_attribute_value_id
        ]
        return {
            "id": product.id,
            "template_id": template.id,
            "sku": product.default_code or None,
            "barcode": product.barcode or None,
            "name": product.with_context(display_default_code=False).display_name,
            "description": template.description_sale or None,
            "category": {"id": template.categ_id.id, "name": template.categ_id.complete_name},
            "type": "service" if template.type == "service" else "goods",
            "uom": product.uom_id.name,
            "variant_attributes": variant_values,
            "active": product.active and template.active,
            "published": True,
            **prices,
            "taxes": [{"name": tax.name, "rate": tax.amount, "included_in_price": tax.price_include} for tax in taxes],
            "etims_tax_type": tax_type,
            "track_stock": bool(product.is_storable),
            "available_quantity": available,
            "weight": product.weight or None,
            "image_url": f"/api/ke/v1/products/{product.id}/image" if template.image_128 else None,
            "updated_at": format_datetime(max(product.write_date, template.write_date)),
        }

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    def _api_stock_levels(self, products, warehouse=None):
        self.ensure_one()
        available = self._api_available_qty(products, warehouse)
        return [
            {
                "product_id": product.id,
                "sku": product.default_code or None,
                "track_stock": bool(product.is_storable),
                "available_quantity": available[product.id],
                "in_stock": available[product.id] is None or available[product.id] > 0,
            }
            for product in products
        ]

    def _api_warehouse_payload(self, warehouse):
        partner = warehouse.partner_id
        return {
            "id": warehouse.id,
            "code": warehouse.code,
            "name": warehouse.name,
            "street": partner.street or None,
            "city": partner.city or None,
            "county": partner.state_id.name or None,
            "county_code": partner.state_id.code or None,
            "phone": partner.phone or None,
        }

    def _api_categories(self):
        self.ensure_one()
        categories = self.env["product.category"].search([])
        return [
            {"id": categ.id, "name": categ.name, "full_name": categ.complete_name, "parent_id": categ.parent_id.id or None}
            for categ in categories
        ]

    def _api_counties(self):
        kenya = self.env.ref("base.ke")
        states = self.env["res.country.state"].search([("country_id", "=", kenya.id)], order="code")
        return [{"code": state.code, "name": state.name} for state in states]

    # ------------------------------------------------------------------
    # Stock change notifications
    # ------------------------------------------------------------------
    def _cron_stock_sync(self):
        """Compare published stock levels with the last values sent and
        notify the platform of the changes (one event per batch)."""
        channels = self.search([("active", "=", True), ("webhook_url", "!=", False)])
        for channel in channels.filtered(lambda c: c._subscribed("stock.updated")):
            api_channel = channel._api_env()
            products = api_channel.env["product.product"].search(
                api_channel._api_product_domain() & Domain("is_storable", "=", True)
            )
            Snapshot = self.env["ke.ecommerce.stock.snapshot"].sudo()
            snapshots = {s.product_id.id: s for s in Snapshot.search([("channel_id", "=", channel.id)])}
            changes = []
            for batch_start in range(0, len(products), 500):
                batch = products[batch_start : batch_start + 500]
                available = api_channel._api_available_qty(batch)
                for product in batch:
                    qty = available[product.id]
                    snapshot = snapshots.get(product.id)
                    if snapshot and snapshot.quantity == qty:
                        continue
                    changes.append({"product_id": product.id, "sku": product.default_code or None, "available_quantity": qty})
                    if snapshot:
                        snapshot.quantity = qty
                    else:
                        Snapshot.create({"channel_id": channel.id, "product_id": product.id, "quantity": qty})
            for batch_start in range(0, len(changes), 200):
                self.env["ke.ecommerce.webhook.event"]._enqueue(
                    channel, "stock.updated", {"items": changes[batch_start : batch_start + 200]}
                )
            channel.sudo().last_stock_sync_at = fields.Datetime.now()
            self.env["ir.cron"]._commit_progress(1)


class KeEcommerceStockSnapshot(models.Model):
    _name = "ke.ecommerce.stock.snapshot"
    _description = "Stock level last sent to an e-commerce channel"

    channel_id = fields.Many2one("ke.ecommerce.channel", required=True, ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    quantity = fields.Float()

    _channel_product_uniq = models.Constraint("unique(channel_id, product_id)", "One snapshot per product and channel.")
