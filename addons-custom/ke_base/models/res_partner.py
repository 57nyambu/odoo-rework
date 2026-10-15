from odoo import api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.ke_base.tools import (
    format_ke_phone,
    is_valid_kra_pin,
    kra_pin_taxpayer_type,
    normalize_ke_msisdn,
    normalize_kra_pin,
)


class ResPartner(models.Model):
    _inherit = "res.partner"

    # In Kenya the tax identification number printed on invoices and sent
    # to eTIMS is the KRA PIN. It is stored in the standard `vat` field so
    # that every core document (invoices, POS receipts, reports) picks it up;
    # the `base.ke` country record relabels the field to "KRA PIN".
    ke_is_kenyan = fields.Boolean(compute="_compute_ke_is_kenyan")
    ke_taxpayer_type = fields.Selection(
        [("individual", "Individual (A-PIN)"), ("non_individual", "Non-individual (P-PIN)")],
        string="KRA taxpayer type",
        compute="_compute_ke_taxpayer_type",
    )
    ke_national_id = fields.Char(
        string="National ID / Passport",
        help="Optional. Used for walk-in customers who do not have a KRA PIN.",
    )

    @api.depends("country_id")
    def _compute_ke_is_kenyan(self):
        for partner in self:
            partner.ke_is_kenyan = partner.country_id.code == "KE"

    @api.depends("vat", "country_id")
    def _compute_ke_taxpayer_type(self):
        for partner in self:
            partner.ke_taxpayer_type = (
                kra_pin_taxpayer_type(partner.vat) if partner.country_id.code == "KE" else False
            )

    @api.model
    def ke_normalize_phone(self, phone):
        """Normalize a Kenyan mobile number to ``2547XXXXXXXX`` (Daraja
        format). Kept as an ORM method for callers that only have an env
        (e.g. JS RPC); Python code can import ``normalize_ke_msisdn``."""
        return normalize_ke_msisdn(phone)

    @api.model
    def ke_format_phone(self, phone):
        return format_ke_phone(phone)

    def _ke_mpesa_msisdn(self):
        """Best phone number to send an M-Pesa STK push to."""
        self.ensure_one()
        return normalize_ke_msisdn(self.phone) or normalize_ke_msisdn(
            self.commercial_partner_id.phone
        )

    @api.model
    def _ke_kenya_country(self):
        return self.env.ref("base.ke", raise_if_not_found=False)

    def _ke_prepare_vals(self, vals):
        """Clean KRA PINs before they hit the database so lookups by PIN
        (eTIMS buyer PIN, API customer matching) are exact."""
        if vals.get("vat"):
            country_id = vals.get("country_id")
            kenya = self._ke_kenya_country()
            is_ke = (country_id and kenya and country_id == kenya.id) or (
                not country_id and self and all(p.country_id.code == "KE" for p in self)
            )
            if is_ke:
                vals["vat"] = normalize_kra_pin(vals["vat"])
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._ke_prepare_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._ke_prepare_vals(vals)
        return super().write(vals)

    @api.constrains("vat", "country_id")
    def _check_ke_kra_pin(self):
        for partner in self:
            if partner.country_id.code != "KE" or not partner.vat:
                continue
            if not is_valid_kra_pin(partner.vat):
                raise ValidationError(
                    self.env._(
                        "%(pin)s is not a valid KRA PIN for %(partner)s. A KRA PIN has 11 "
                        "characters: 'A' (individuals) or 'P' (companies), 9 digits, then a "
                        "letter, e.g. P051234567X.",
                        pin=partner.vat,
                        partner=partner.display_name,
                    )
                )
