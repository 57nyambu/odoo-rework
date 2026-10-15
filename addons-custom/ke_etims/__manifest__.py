{
    "name": "Kenya eTIMS",
    "summary": "KRA eTIMS (OSCU/VSCU) fiscalization for invoices and credit notes: "
    "pluggable connector, retry queue, tax type mapping, QR on documents.",
    "version": "19.0.2.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["ke_base", "account", "l10n_ke", "product"],
    "data": [
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "views/res_config_settings_views.xml",
        "views/account_move_views.xml",
        "views/account_tax_views.xml",
        "views/product_template_views.xml",
        "report/report_invoice.xml",
    ],
    "installable": True,
}
