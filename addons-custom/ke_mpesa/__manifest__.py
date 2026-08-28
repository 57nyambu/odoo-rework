{
    "name": "Kenya M-Pesa Payments",
    "summary": "M-Pesa STK Push payment method for POS, via Safaricom's Daraja API.",
    "version": "19.0.1.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["ke_base", "point_of_sale", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/mpesa_transaction_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "ke_mpesa/static/src/js/payment_mpesa.js",
        ],
    },
}
