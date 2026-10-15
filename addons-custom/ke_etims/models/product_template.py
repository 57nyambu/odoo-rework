from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # Item master data KRA requires when registering an item with eTIMS.
    # Codes come from the KRA eTIMS code lists; the defaults below are the
    # generic ones ("NT" = net / no packaging, "U" = pieces/items).
    ke_etims_item_class_code = fields.Char(
        string="eTIMS item classification",
        help="KRA item classification code (UNSPSC-based), e.g. 5020230100 for "
        "carbonated soft drinks. Required by most connectors before items can be "
        "transmitted.",
    )
    ke_etims_product_type = fields.Selection(
        [("1", "Raw material"), ("2", "Finished product"), ("3", "Service")],
        string="eTIMS product type",
        compute="_compute_ke_etims_product_type",
        store=True,
        readonly=False,
        precompute=True,
    )
    ke_etims_packaging_unit = fields.Char(
        string="eTIMS packaging unit", default="NT", help="KRA packaging unit code."
    )
    ke_etims_quantity_unit = fields.Char(
        string="eTIMS quantity unit", default="U", help="KRA quantity unit code."
    )
    ke_etims_origin_country_id = fields.Many2one(
        "res.country",
        string="Country of origin",
        default=lambda self: self.env.ref("base.ke", raise_if_not_found=False),
    )
    ke_etims_item_code = fields.Char(
        string="eTIMS item code",
        copy=False,
        help="Item code assigned when the product was registered on eTIMS "
        "(e.g. KE2NTU0000001). Filled in by the connector.",
    )

    @api.depends("type")
    def _compute_ke_etims_product_type(self):
        for template in self:
            if template.type == "service":
                template.ke_etims_product_type = "3"
            elif template.ke_etims_product_type in (False, "3"):
                template.ke_etims_product_type = "2"
            else:
                # keep a manual "raw material" choice
                template.ke_etims_product_type = template.ke_etims_product_type
