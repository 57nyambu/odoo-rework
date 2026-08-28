from odoo import fields, models, tools


class KeDailySalesReport(models.Model):
    """Read-only, SQL-view-backed report — the standard Odoo pattern for
    custom reporting: no data is duplicated, the view re-aggregates
    pos_order/pos_payment on every query.
    """

    _name = "ke.daily.sales.report"
    _description = "Daily sales summary (cash vs M-Pesa, VAT collected)"
    _auto = False
    _order = "sale_date desc"

    sale_date = fields.Date(readonly=True)
    payment_method = fields.Char(readonly=True)
    total_amount = fields.Monetary(readonly=True)
    tax_amount = fields.Monetary(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            """
            CREATE VIEW %s AS (
                SELECT
                    row_number() OVER () AS id,
                    o.date_order::date AS sale_date,
                    pm.name AS payment_method,
                    SUM(p.amount) AS total_amount,
                    SUM(o.amount_tax) AS tax_amount,
                    o.currency_id AS currency_id
                FROM pos_order o
                JOIN pos_payment p ON p.pos_order_id = o.id
                JOIN pos_payment_method pm ON pm.id = p.payment_method_id
                WHERE o.state IN ('paid', 'done', 'invoiced')
                GROUP BY o.date_order::date, pm.name, o.currency_id
            )
            """
            % self._table
            # NOTE: pm.name above assumes pos_payment_method.name is a
            # plain (non-translated) column. If your install has it as a
            # translated field, Odoo 17+ stores those as jsonb and this
            # needs pm.name->>'en_US' instead — check with
            # `\d pos_payment_method` in psql before relying on this.
        )
