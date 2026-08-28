{
    "name": "Kenya UAC Extensions",
    "summary": "Custom security groups and record rules layered on top of standard Odoo access control.",
    "version": "19.0.1.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "stock", "purchase", "account"],
    "data": [
        "security/security_groups.xml",
        "security/record_rules.xml",
    ],
}
