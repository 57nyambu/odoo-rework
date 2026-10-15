{
    "name": "Kenya UAC Extensions",
    "summary": "Retail roles (cashier, branch manager) and per-cashier shop restriction "
    "layered on standard Odoo access control.",
    "version": "19.0.2.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock", "purchase", "account"],
    "data": [
        "security/security_groups.xml",
        "security/record_rules.xml",
        "views/res_users_views.xml",
    ],
    "installable": True,
}
