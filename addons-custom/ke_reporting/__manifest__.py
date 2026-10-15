{
    "name": "Kenya Reporting",
    "summary": "Daily collections (cash vs M-Pesa) and VAT by KRA eTIMS tax category.",
    "version": "19.0.2.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "ke_etims"],
    "data": [
        "security/ir.model.access.csv",
        "security/ke_reporting_security.xml",
        "views/ke_dashboard_views.xml",
    ],
}
