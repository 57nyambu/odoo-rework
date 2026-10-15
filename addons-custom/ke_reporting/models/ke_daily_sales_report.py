from odoo import fields, models, tools

from odoo.addons.ke_etims.models.account_tax import ETIMS_TAX_TYPES


class KeDailySalesReport(models.Model):
    """Money collected per day, shop and payment method (cash vs M-Pesa vs
    card...). SQL view: nothing is duplicated, every query re-aggregates
    pos_payment."""

    _name = "ke.daily.sales.report"
    _description = "Daily collections by payment method (POS)"
    _auto = False
    _order = "sale_date desc"

    sale_date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    config_id = fields.Many2one("pos.config", string="Shop", readonly=True)
    session_id = fields.Many2one("pos.session", readonly=True)
    payment_method_id = fields.Many2one("pos.payment.method", readonly=True)
    order_count = fields.Integer(string="Orders", readonly=True, aggregator="sum")
    amount = fields.Monetary(string="Collected", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        # Dates are grouped in East Africa Time, not UTC: a sale at 01:00 EAT
        # belongs to that day's takings.
        self.env.cr.execute(
            f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    MIN(p.id) AS id,
                    (p.payment_date AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Nairobi')::date AS sale_date,
                    o.company_id AS company_id,
                    s.config_id AS config_id,
                    o.session_id AS session_id,
                    p.payment_method_id AS payment_method_id,
                    COUNT(DISTINCT o.id) AS order_count,
                    SUM(p.amount) AS amount,
                    rc.currency_id AS currency_id
                FROM pos_payment p
                JOIN pos_order o ON o.id = p.pos_order_id
                JOIN pos_session s ON s.id = o.session_id
                JOIN res_company rc ON rc.id = o.company_id
                WHERE o.state IN ('paid', 'done')
                GROUP BY 2, 3, 4, 5, 6, 9
            )
            """
        )


class KeVatSalesReport(models.Model):
    """Taxable sales and VAT per day and KRA eTIMS tax category (A exempt,
    B 16%, C zero rated, D non-VAT, E 8%) for the VAT return (VAT3)."""

    _name = "ke.vat.sales.report"
    _description = "POS sales by eTIMS tax category"
    _auto = False
    _order = "sale_date desc"

    sale_date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    config_id = fields.Many2one("pos.config", string="Shop", readonly=True)
    etims_tax_type = fields.Selection(ETIMS_TAX_TYPES, string="eTIMS tax type", readonly=True)
    taxable_amount = fields.Monetary(readonly=True)
    tax_amount = fields.Monetary(string="VAT", readonly=True)
    total_amount = fields.Monetary(string="Total incl. VAT", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    MIN(l.id) AS id,
                    (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Nairobi')::date AS sale_date,
                    o.company_id AS company_id,
                    s.config_id AS config_id,
                    COALESCE(
                        (SELECT t.ke_etims_tax_type
                           FROM account_tax_pos_order_line_rel rel
                           JOIN account_tax t ON t.id = rel.account_tax_id
                          WHERE rel.pos_order_line_id = l.id AND t.ke_etims_tax_type IS NOT NULL
                          ORDER BY t.sequence, t.id
                          LIMIT 1),
                        'D'
                    ) AS etims_tax_type,
                    SUM(l.price_subtotal) AS taxable_amount,
                    SUM(l.price_subtotal_incl - l.price_subtotal) AS tax_amount,
                    SUM(l.price_subtotal_incl) AS total_amount,
                    rc.currency_id AS currency_id
                FROM pos_order_line l
                JOIN pos_order o ON o.id = l.order_id
                JOIN pos_session s ON s.id = o.session_id
                JOIN res_company rc ON rc.id = o.company_id
                WHERE o.state IN ('paid', 'done')
                GROUP BY 2, 3, 4, 5, 9
            )
            """
        )
