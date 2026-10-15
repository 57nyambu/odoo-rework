{
    "name": "Kenya M-Pesa (Daraja)",
    "summary": "M-Pesa STK push, till/paybill (C2B) payments, callbacks and "
    "reconciliation helpers via Safaricom Daraja. Used by POS and e-commerce.",
    "version": "19.0.2.0.0",
    "category": "Kenya Localization",
    "author": "Tom Nyambu",
    "license": "LGPL-3",
    "depends": ["ke_base", "account"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ke_mpesa_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/mpesa_transaction_views.xml",
        "views/mpesa_account_views.xml",
    ],
    "installable": True,
}
