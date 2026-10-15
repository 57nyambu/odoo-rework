from collections import defaultdict

from odoo import Command, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import float_compare, float_round, html2plaintext

from odoo.addons.ke_base.tools import (
    is_valid_kra_pin,
    is_valid_mpesa_code,
    normalize_ke_msisdn,
    normalize_kra_pin,
    normalize_mpesa_code,
)

from .api_tools import ApiError, as_float, format_date, format_datetime, parse_datetime

FULFILMENT_TYPES = ("delivery", "pickup")
PAYMENT_METHODS = ("mpesa", "airtel_money", "card", "bank_transfer", "cash_on_delivery", "other")


class KeEcommerceChannel(models.Model):
    _inherit = "ke.ecommerce.channel"

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------
    def _api_find_or_create_customer(self, data):
        """Match the online customer with an Odoo contact (channel customer
        id, then e-mail, then phone) or create it. Returns res.partner."""
        self.ensure_one()
        if not isinstance(data, dict) or not (data.get("name") or data.get("company_name")):
            raise ApiError(422, "validation_error", "customer.name is required.", {"customer.name": "required"})
        Partner = self.env["res.partner"]
        email = (data.get("email") or "").strip().lower() or False
        msisdn = normalize_ke_msisdn(data.get("phone"))
        if data.get("phone") and not msisdn:
            raise ApiError(422, "invalid_phone", "customer.phone is not a valid Kenyan mobile number.",
                           {"customer.phone": data.get("phone")})
        kra_pin = normalize_kra_pin(data.get("kra_pin")) or False
        if kra_pin and not is_valid_kra_pin(kra_pin):
            raise ApiError(422, "invalid_kra_pin",
                           "customer.kra_pin must look like A012345678Z (individual) or P051234567X (company).",
                           {"customer.kra_pin": data.get("kra_pin")})
        external_id = str(data.get("external_id") or "").strip() or False
        base = Domain("company_id", "in", (False, self.company_id.id)) & Domain("parent_id", "=", False)

        partner = Partner.browse()
        if external_id:
            partner = Partner.search(
                base & Domain("ke_ecommerce_channel_id", "=", self.id) & Domain("ke_ecommerce_ref", "=", external_id),
                limit=1,
            )
        if not partner and email:
            partner = Partner.search(base & Domain("email", "=ilike", email), limit=1)
        if not partner and msisdn:
            candidates = Partner.search(base & Domain("phone", "ilike", msisdn[-9:]), limit=20)
            partner = candidates.filtered(lambda p: normalize_ke_msisdn(p.phone) == msisdn)[:1]

        kenya = self.env.ref("base.ke")
        is_company = bool(data.get("company_name"))
        vals = {
            "name": data.get("company_name") or data.get("name"),
            "is_company": is_company,
        }
        if email:
            vals["email"] = email
        if msisdn:
            vals["phone"] = f"+{msisdn}"
        if kra_pin:
            vals["vat"] = kra_pin
        if not partner:
            vals.update(
                {
                    "country_id": kenya.id,
                    "company_id": self.company_id.id,
                    "ke_ecommerce_channel_id": self.id,
                    "ke_ecommerce_ref": external_id,
                    "customer_rank": 1,
                }
            )
            partner = Partner.create(vals)
        else:
            updates = {key: value for key, value in vals.items() if key in ("email", "phone") and not partner[key]}
            if kra_pin and partner.vat != kra_pin:
                updates.update({"vat": kra_pin, "country_id": partner.country_id.id or kenya.id})
            if external_id and not partner.ke_ecommerce_ref:
                updates.update({"ke_ecommerce_ref": external_id, "ke_ecommerce_channel_id": self.id})
            if updates:
                partner.write(updates)
        return partner

    def _api_resolve_county(self, value):
        if not value:
            return self.env["res.country.state"]
        kenya = self.env.ref("base.ke")
        text = str(value).strip()
        if text.isdigit():
            text = f"KE-{int(text):02d}"
        State = self.env["res.country.state"]
        base = Domain("country_id", "=", kenya.id)
        state = State.search(base & (Domain("code", "=ilike", text) | Domain("name", "=ilike", text)), limit=1)
        if not state:
            state = State.search(base & Domain("name", "ilike", text), limit=1)
        if not state:
            raise ApiError(422, "invalid_county", f"Unknown county '{value}'. See GET /api/ke/v1/counties.",
                           {"shipping_address.county": value})
        return state

    def _api_shipping_partner(self, customer, address):
        if not address:
            return customer
        if not isinstance(address, dict):
            raise ApiError(422, "validation_error", "shipping_address must be an object.")
        state = self._api_resolve_county(address.get("county"))
        country = self.env.ref("base.ke")
        if address.get("country") and str(address["country"]).upper() != "KE":
            country = self.env["res.country"].search([("code", "=ilike", address["country"])], limit=1) or country
        msisdn = normalize_ke_msisdn(address.get("phone"))
        vals = {
            "type": "delivery",
            "parent_id": customer.id,
            "name": address.get("name") or customer.name,
            "phone": f"+{msisdn}" if msisdn else address.get("phone") or False,
            "street": address.get("street") or False,
            "street2": address.get("street2") or False,
            "city": address.get("city") or False,
            "zip": address.get("postal_code") or False,
            "state_id": state.id or False,
            "country_id": country.id,
        }
        existing = customer.child_ids.filtered(
            lambda c: c.type == "delivery"
            and (c.street or False, c.street2 or False, c.city or False, c.state_id.id or False, c.name)
            == (vals["street"], vals["street2"], vals["city"], vals["state_id"], vals["name"])
        )[:1]
        if existing:
            if vals["phone"] and existing.phone != vals["phone"]:
                existing.phone = vals["phone"]
            return existing
        return self.env["res.partner"].create(vals)

    def _api_customer_payload(self, partner):
        return {
            "id": partner.id,
            "external_id": partner.ke_ecommerce_ref or None,
            "name": partner.name,
            "email": partner.email or None,
            "phone": partner.phone or None,
            "kra_pin": partner.vat or None,
            "is_company": partner.is_company,
        }

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    def _api_find_order(self, ref):
        self.ensure_one()
        order = self.env["sale.order"].search(
            [("ke_channel_id", "=", self.id), ("ke_external_ref", "=", str(ref))], limit=1
        )
        if not order:
            raise ApiError(404, "not_found", f"Order '{ref}' does not exist on this channel.")
        return order

    def _api_resolve_line(self, index, line):
        where = f"lines[{index}]"
        if not isinstance(line, dict):
            raise ApiError(422, "validation_error", f"{where} must be an object.")
        quantity = as_float(line.get("quantity"), f"{where}.quantity", required=True)
        if quantity <= 0:
            raise ApiError(422, "validation_error", f"{where}.quantity must be positive.", {f"{where}.quantity": quantity})
        Product = self.env["product.product"]
        domain = self._api_product_domain() & Domain("active", "=", True)
        product = Product.browse()
        if line.get("product_id"):
            product = Product.search(domain & Domain("id", "=", int(line["product_id"])), limit=1)
        elif line.get("sku"):
            product = Product.search(domain & Domain("default_code", "=", str(line["sku"])), limit=1)
        elif line.get("barcode"):
            product = Product.search(domain & Domain("barcode", "=", str(line["barcode"])), limit=1)
        if not product:
            ident = line.get("product_id") or line.get("sku") or line.get("barcode")
            raise ApiError(422, "unknown_product", f"{where}: product '{ident}' is not published on this channel.",
                           {where: ident})
        discount = as_float(line.get("discount_percent"), f"{where}.discount_percent", minimum=0) or 0.0
        if discount > 100:
            raise ApiError(422, "validation_error", f"{where}.discount_percent cannot exceed 100.")
        return {
            "where": where,
            "product": product,
            "quantity": quantity,
            "unit_price": as_float(line.get("unit_price"), f"{where}.unit_price", minimum=0),
            "discount": discount,
            "name": line.get("name"),
        }

    def _api_check_stock(self, lines, warehouse):
        if self.allow_backorder:
            return
        needed = defaultdict(float)
        for line in lines:
            if line["product"].is_storable:
                needed[line["product"]] += line["quantity"]
        if not needed:
            return
        products = self.env["product.product"].union(*needed.keys())
        available = self._api_available_qty(products, warehouse)
        shortages = [
            {
                "product_id": product.id,
                "sku": product.default_code or None,
                "requested": qty,
                "available": available[product.id],
            }
            for product, qty in needed.items()
            if available[product.id] is not None and float_compare(qty, available[product.id], precision_digits=4) > 0
        ]
        if shortages:
            raise ApiError(409, "insufficient_stock", "Some items are not available in the requested quantity.",
                           {"items": shortages})

    def _api_taxes(self, product, partner):
        taxes = product.taxes_id._filter_taxes_by_company(self.company_id)
        fiscal_position = self.env["account.fiscal.position"]._get_fiscal_position(partner)
        return fiscal_position.map_tax(taxes) if fiscal_position else taxes

    def _api_price_unit_from(self, amount, taxes, amount_includes_tax):
        """Convert a price from the platform into Odoo's ``price_unit`` for
        ``taxes`` (which may themselves be price-included or not). Handles
        percentage taxes, the only kind used for Kenyan VAT."""
        percent = taxes.filtered(lambda t: t.amount_type == "percent")
        included_rate = sum(percent.filtered("price_include").mapped("amount")) / 100.0
        total_rate = sum(percent.mapped("amount")) / 100.0
        net = amount / (1.0 + total_rate) if amount_includes_tax else amount
        return float_round(net * (1.0 + included_rate), precision_digits=6)

    def _api_line_vals(self, line, prices_include_tax, partner, warnings):
        product = line["product"]
        taxes = self._api_taxes(product, partner)
        currency = self.pricelist_id.currency_id
        odoo_price = self.pricelist_id._get_product_price(product, line["quantity"], currency=currency)
        price_unit = odoo_price
        if line["unit_price"] is not None:
            if self.price_source == "platform":
                price_unit = self._api_price_unit_from(line["unit_price"], taxes, prices_include_tax)
            else:
                computed = taxes.compute_all(odoo_price, currency=currency, quantity=1.0, product=product)
                expected = computed["total_included"] if prices_include_tax else computed["total_excluded"]
                if currency.compare_amounts(expected, line["unit_price"]) != 0:
                    warnings.append(
                        {
                            "code": "price_overridden",
                            "field": f"{line['where']}.unit_price",
                            "message": f"Platform price {line['unit_price']:.2f} differs from the Odoo price "
                            f"{expected:.2f}; the Odoo price was used.",
                        }
                    )
        vals = {
            "product_id": product.id,
            "product_uom_qty": line["quantity"],
            "price_unit": price_unit,
            "discount": line["discount"],
            "tax_ids": [Command.set(taxes.ids)],
        }
        if line["name"]:
            vals["name"] = line["name"]
        return vals

    def _api_create_order(self, data):
        """Create (or return the existing) sale order for an online order.

        :return: (sale.order, created: bool, warnings: list)
        """
        self.ensure_one()
        if not isinstance(data, dict):
            raise ApiError(400, "invalid_json", "The request body must be a JSON object.")
        external_id = str(data.get("external_id") or "").strip()
        if not external_id or len(external_id) > 64:
            raise ApiError(422, "validation_error", "external_id is required (max 64 characters).",
                           {"external_id": "required"})
        SaleOrder = self.env["sale.order"]
        existing = SaleOrder.search([("ke_channel_id", "=", self.id), ("ke_external_ref", "=", external_id)], limit=1)
        if existing:
            return existing, False, []

        currency = self.pricelist_id.currency_id
        if data.get("currency") and data["currency"].upper() != currency.name:
            raise ApiError(422, "currency_mismatch", f"This channel sells in {currency.name}.",
                           {"currency": data["currency"]})
        lines_data = data.get("lines")
        if not isinstance(lines_data, list) or not lines_data:
            raise ApiError(422, "validation_error", "lines must contain at least one item.", {"lines": "required"})

        fulfilment = data.get("fulfilment") or {}
        fulfilment_type = fulfilment.get("type") or "delivery"
        if fulfilment_type not in FULFILMENT_TYPES:
            raise ApiError(422, "validation_error", "fulfilment.type must be 'delivery' or 'pickup'.")
        warehouse = self.warehouse_id
        if fulfilment_type == "pickup":
            location = str(fulfilment.get("pickup_location_id") or "")
            warehouse = self.pickup_warehouse_ids.filtered(lambda w: str(w.id) == location or w.code == location)[:1]
            if not warehouse:
                raise ApiError(422, "invalid_pickup_location",
                               "fulfilment.pickup_location_id is not a pickup branch of this channel. "
                               "See GET /api/ke/v1/pickup-locations.",
                               {"fulfilment.pickup_location_id": location})

        # Cheap validations first, then customer records.
        lines = [self._api_resolve_line(index, line) for index, line in enumerate(lines_data)]
        self._api_check_stock(lines, warehouse)
        customer = self._api_find_or_create_customer(data.get("customer"))
        shipping = customer
        if fulfilment_type == "delivery":
            shipping = self._api_shipping_partner(customer, data.get("shipping_address"))

        prices_include_tax = data.get("prices_include_tax", True) is not False
        warnings = []
        order_lines = [Command.create(self._api_line_vals(line, prices_include_tax, customer, warnings)) for line in lines]

        shipping_fee = as_float(fulfilment.get("shipping_fee"), "fulfilment.shipping_fee", minimum=0)
        if shipping_fee:
            product = self.sudo().shipping_product_id
            taxes = self._api_taxes(product, customer)
            order_lines.append(
                Command.create(
                    {
                        "product_id": product.id,
                        "name": self.env._("Delivery%s", f" ({fulfilment['carrier']})" if fulfilment.get("carrier") else ""),
                        "product_uom_qty": 1.0,
                        "price_unit": self._api_price_unit_from(shipping_fee, taxes, prices_include_tax),
                        "tax_ids": [Command.set(taxes.ids)],
                    }
                )
            )

        order = SaleOrder.create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": shipping.id,
                "pricelist_id": self.pricelist_id.id,
                "warehouse_id": warehouse.id,
                "team_id": self.team_id.id,
                "company_id": self.company_id.id,
                "client_order_ref": external_id,
                "origin": f"{self.name}: {external_id}",
                "ke_channel_id": self.id,
                "ke_external_ref": external_id,
                "ke_fulfilment_type": fulfilment_type,
                "ke_carrier": fulfilment.get("carrier") or False,
                "ke_delivery_instructions": fulfilment.get("instructions") or False,
                "ke_placed_at": parse_datetime(data.get("placed_at"), "placed_at") or fields.Datetime.now(),
                "note": data.get("note") or False,
                "order_line": order_lines,
            }
        )
        self.env["ke.ecommerce.webhook.event"]._enqueue(self, "order.created", self._api_order_payload(order), order)
        if self.auto_confirm:
            order.action_confirm()
        if data.get("payment"):
            self._api_register_payment(order, data["payment"])
        return order, True, warnings

    def _api_cancel_order(self, order, reason=None):
        self.ensure_one()
        if order.state == "cancel":
            return order
        if order.picking_ids.filtered(lambda p: p.state == "done" and p.picking_type_code == "outgoing"):
            raise ApiError(409, "already_fulfilled", "Goods were already delivered; create a return in Odoo instead.")
        posted = order.invoice_ids.filtered(lambda m: m.state == "posted" and m.move_type == "out_invoice")
        if posted:
            # The invoice is a fiscal document (eTIMS): reverse it with a
            # credit note instead of deleting it.
            reversal = self.env["account.move.reversal"].with_context(
                active_model="account.move", active_ids=posted.ids
            ).create({"reason": reason or self.env._("Online order cancelled"), "journal_id": posted[0].journal_id.id})
            reversal.refund_moves()
            posted.reversal_move_ids.filtered(lambda m: m.state == "draft").action_post()
        order.with_context(disable_cancel_warning=True).action_cancel()
        if reason:
            order.message_post(body=self.env._("Cancelled from %(channel)s: %(reason)s", channel=self.name, reason=reason))
        return order

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    def _api_payment_journal(self, method):
        journal = (self.mpesa_journal_id if method == "mpesa" else False) or self.payment_journal_id
        if not journal:
            raise ApiError(422, "no_payment_journal", "No payment journal is configured on this channel.")
        return journal

    def _api_verify_mpesa(self, order, code, amount):
        """Optionally match an M-Pesa code against Safaricom's notification."""
        if self.mpesa_verification == "none" or not self.mpesa_account_id:
            return self.env["ke.mpesa.transaction"]
        Tx = self.env["ke.mpesa.transaction"].sudo()
        tx = Tx._find_claimable(code, self.mpesa_account_id)
        if not tx:
            if self.mpesa_verification == "required":
                raise ApiError(422, "mpesa_payment_not_found",
                               f"No M-Pesa payment {code} has been received on {self.mpesa_account_id.display_number}.")
            return Tx.browse()
        if tx.claimed and tx.ke_sale_order_id != order:
            raise ApiError(409, "mpesa_payment_already_used", f"M-Pesa payment {code} was already used.")
        if self.currency_id.compare_amounts(tx.amount, amount) < 0:
            raise ApiError(422, "mpesa_amount_mismatch",
                           f"M-Pesa payment {code} is for {tx.amount:.2f}, less than {amount:.2f}.")
        if not tx.claimed:
            tx._claim(order.name)
        tx.ke_sale_order_id = order.id
        return tx

    def _api_register_payment(self, order, data, transaction=None):
        """Book a payment received online against ``order``. Idempotent on the
        payment reference. Returns (account.payment, created: bool).

        ``transaction`` is passed when Odoo itself collected the money (STK
        push confirmed by Safaricom): the M-Pesa checks are then skipped."""
        self.ensure_one()
        if not isinstance(data, dict):
            raise ApiError(422, "validation_error", "payment must be an object.")
        method = data.get("method") or "other"
        if method not in PAYMENT_METHODS:
            raise ApiError(422, "validation_error", f"payment.method must be one of {', '.join(PAYMENT_METHODS)}.")
        reference = str(data.get("reference") or "").strip()
        if not reference:
            raise ApiError(422, "validation_error", "payment.reference is required.", {"payment.reference": "required"})
        if method == "mpesa" and not transaction:
            reference = normalize_mpesa_code(reference)
            if not is_valid_mpesa_code(reference):
                raise ApiError(422, "invalid_mpesa_code", "payment.reference must be a 10 character M-Pesa code.")
        Payment = self.env["account.payment"]
        existing = Payment.search([("ke_channel_id", "=", self.id), ("ke_external_ref", "=", reference)], limit=1)
        if existing:
            return existing, False
        if order.state == "cancel":
            raise ApiError(409, "order_cancelled", "The order is cancelled.")
        amount = as_float(data.get("amount"), "payment.amount", required=True)
        if amount <= 0:
            raise ApiError(422, "validation_error", "payment.amount must be positive.")
        if method == "mpesa" and not transaction:
            transaction = self._api_verify_mpesa(order, reference, amount)
        paid_at = parse_datetime(data.get("paid_at"), "payment.paid_at") or fields.Datetime.now()
        journal = self._api_payment_journal(method)
        payment = Payment.with_context(force_payment_move=True).create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": order.partner_invoice_id.commercial_partner_id.id,
                "amount": amount,
                "currency_id": order.currency_id.id,
                "journal_id": journal.id,
                "date": fields.Date.context_today(self, paid_at),
                "memo": f"{order.name} {method.replace('_', ' ').upper()} {reference}",
                "payment_reference": reference,
                "ke_channel_id": self.id,
                "ke_sale_order_id": order.id,
                "ke_external_ref": reference,
                "ke_payment_method": method,
                "ke_payer_phone": normalize_ke_msisdn(data.get("phone")) or False,
            }
        )
        payment.action_post()
        if transaction:
            transaction.sudo().ke_account_payment_id = payment.id
        self.env["ke.ecommerce.webhook.event"]._enqueue(
            self, "payment.received", {"order": self._api_order_ref(order), "payment": self._api_payment_payload(payment)}
        )
        order._ke_after_payment()
        return payment, True

    def _api_stk_push(self, order, data):
        self.ensure_one()
        if not self.mpesa_account_id:
            raise ApiError(422, "mpesa_not_configured", "This channel has no M-Pesa account for STK push.")
        amount = as_float(data.get("amount"), "amount") or order.ke_amount_due
        if amount <= 0:
            raise ApiError(409, "nothing_to_pay", "The order is already paid.")
        phone = data.get("phone") or order.partner_id.phone
        try:
            tx = self.mpesa_account_id.sudo().stk_push(
                phone,
                amount,
                reference=order.name.replace("/", "")[:12],
                description=self.name[:13],
                extra_vals={"ke_sale_order_id": order.id},
            )
        except UserError as error:
            raise ApiError(422, "mpesa_request_failed", str(error)) from error
        return tx

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def _api_order_ref(self, order):
        return {"id": order.id, "name": order.name, "external_id": order.ke_external_ref}

    def _api_payment_payload(self, payment):
        return {
            "id": payment.id,
            "method": payment.ke_payment_method or "other",
            "reference": payment.ke_external_ref or payment.payment_reference,
            "amount": payment.amount,
            "currency": payment.currency_id.name,
            "date": format_date(payment.date),
            "state": payment.state,
        }

    def _api_invoice_payload(self, move):
        etims = None
        if "ke_etims_state" in move._fields:
            etims = {
                "status": move.ke_etims_state,
                "cu_invoice_number": move.ke_etims_invoice_number or None,
                "scu_id": move.ke_etims_scu_id or None,
                "receipt_number": move.ke_etims_receipt_number or None,
                "signed_at": format_datetime(move.ke_etims_signed_at),
                "qr_data": move.ke_etims_qr_data or None,
                "training": move.ke_etims_training,
            }
        return {
            "id": move.id,
            "number": move.name,
            "type": "credit_note" if move.move_type == "out_refund" else "invoice",
            "state": move.state,
            "date": format_date(move.invoice_date),
            "amount_untaxed": move.amount_untaxed,
            "amount_tax": move.amount_tax,
            "amount_total": move.amount_total,
            "amount_residual": move.amount_residual,
            "payment_state": move.payment_state,
            "etims": etims,
            "portal_url": move.get_base_url() + move.get_portal_url() if move.state == "posted" else None,
        }

    def _api_order_payload(self, order):
        self.ensure_one()
        currency = order.currency_id
        Tax = self.env["account.tax"]
        lines = []
        tax_breakdown = defaultdict(lambda: {"base": 0.0, "amount": 0.0})
        for line in order.order_line.filtered(lambda l: not l.display_type):
            tax_type = Tax._ke_etims_tax_type_for(line.tax_ids)
            tax_breakdown[tax_type]["base"] += line.price_subtotal
            tax_breakdown[tax_type]["amount"] += line.price_tax
            lines.append(
                {
                    "id": line.id,
                    "product_id": line.product_id.id,
                    "sku": line.product_id.default_code or None,
                    "name": line.name,
                    "quantity": line.product_uom_qty,
                    "quantity_delivered": line.qty_delivered,
                    "quantity_invoiced": line.qty_invoiced,
                    "unit_price": line.price_unit,
                    "discount_percent": line.discount,
                    "subtotal": currency.round(line.price_subtotal),
                    "tax": currency.round(line.price_tax),
                    "total": currency.round(line.price_total),
                    "etims_tax_type": tax_type,
                    "is_delivery_fee": line.product_id == self.sudo().shipping_product_id,
                }
            )
        pickings = order.picking_ids.filtered(lambda p: p.state != "cancel")
        invoices = order.invoice_ids.filtered(lambda m: m.state != "cancel")
        pickup = order.warehouse_id if order.ke_fulfilment_type == "pickup" else None
        shipping = order.partner_shipping_id
        return {
            "id": order.id,
            "name": order.name,
            "external_id": order.ke_external_ref,
            "status": order._ke_api_status(),
            "payment_status": order.ke_payment_state,
            "currency": currency.name,
            "amount_untaxed": currency.round(order.amount_untaxed),
            "amount_tax": currency.round(order.amount_tax),
            "amount_total": currency.round(order.amount_total),
            "amount_paid": currency.round(order.ke_amount_paid),
            "amount_due": currency.round(order.ke_amount_due),
            "tax_breakdown": [
                {"etims_tax_type": code, "base": currency.round(vals["base"]), "tax": currency.round(vals["amount"])}
                for code, vals in sorted(tax_breakdown.items())
            ],
            "customer": self._api_customer_payload(order.partner_id),
            "shipping_address": None
            if order.ke_fulfilment_type == "pickup"
            else {
                "name": shipping.name,
                "phone": shipping.phone or None,
                "street": shipping.street or None,
                "street2": shipping.street2 or None,
                "city": shipping.city or None,
                "county": shipping.state_id.name or None,
                "county_code": shipping.state_id.code or None,
                "postal_code": shipping.zip or None,
                "country": shipping.country_id.code or None,
            },
            "fulfilment": {
                "type": order.ke_fulfilment_type or "delivery",
                "carrier": order.ke_carrier or None,
                "instructions": order.ke_delivery_instructions or None,
                "pickup_location": self._api_warehouse_payload(pickup) if pickup else None,
                "shipments": [
                    {
                        "id": picking.id,
                        "name": picking.name,
                        "state": picking.state,
                        "scheduled_date": format_datetime(picking.scheduled_date),
                        "date_done": format_datetime(picking.date_done),
                        "tracking_reference": picking.carrier_tracking_ref or None
                        if "carrier_tracking_ref" in picking._fields
                        else None,
                    }
                    for picking in pickings
                ],
            },
            "lines": lines,
            "invoices": [self._api_invoice_payload(move) for move in invoices],
            "payments": [self._api_payment_payload(payment) for payment in order.ke_payment_ids],
            "note": html2plaintext(order.note) if order.note else None,
            "placed_at": format_datetime(order.ke_placed_at),
            "created_at": format_datetime(order.create_date),
            "updated_at": format_datetime(order.write_date),
        }
