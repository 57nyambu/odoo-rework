{
    "name": "Kenya Point of Sale",
    "summary": "Point of Sale for Kenya: M-Pesa STK push and till/paybill code "
    "verification, KRA eTIMS signed receipts with QR, KRA PIN, KES rounding.",
    "version": "19.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "ke_base", "ke_etims", "ke_mpesa"],
    "data": [
        "security/ke_pos_security.xml",
        "data/account_cash_rounding_data.xml",
        "views/pos_payment_method_views.xml",
        "views/res_config_settings_views.xml",
        "views/pos_order_views.xml",
        "views/ke_mpesa_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "ke_pos/static/src/app/**/*",
        ],
    },
    "post_init_hook": "_ke_pos_post_init",
    "installable": True,
    "application": False,
}
