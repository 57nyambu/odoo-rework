{
    "name": "Kenya E-commerce Exchange API",
    "summary": "REST API + signed webhooks to sync an online shop with Odoo: catalog, "
    "branch stock, orders, M-Pesa/card payments, deliveries and KRA eTIMS invoices.",
    "version": "19.0.1.0.0",
    "category": "Sales/Sales",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["ke_base", "ke_mpesa", "ke_etims", "sale_stock"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ke_ecommerce_security.xml",
        "security/ir.model.access.csv",
        "data/ke_ecommerce_data.xml",
        "views/ecommerce_channel_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
