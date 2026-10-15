# Executed inside `odoo shell` by scripts/tenant.sh (stdin), with `env` bound.
# SETUP_PHASE=company  : before installing accounting (country decides the localization)
# SETUP_PHASE=finalize : after the ke_* modules are installed
import os
import secrets

phase = os.environ.get("SETUP_PHASE")
company = env.ref("base.main_company")  # noqa: F821 - provided by odoo shell
kenya = env.ref("base.ke")  # noqa: F821

if phase == "company":
    kes = env.ref("base.KES")  # noqa: F821
    kes.active = True
    company.write(
        {
            "name": os.environ["COMPANY_NAME"],
            "country_id": kenya.id,
            "currency_id": kes.id,
            "vat": os.environ["KRA_PIN"].strip().upper(),
        }
    )
    admin = env.ref("base.user_admin")  # noqa: F821
    password = secrets.token_urlsafe(14)
    admin.write({"tz": "Africa/Nairobi", "password": password})
    if os.environ.get("ADMIN_EMAIL"):
        admin.write({"login": os.environ["ADMIN_EMAIL"], "email": os.environ["ADMIN_EMAIL"]})
    env["ir.config_parameter"].sudo().set_param("web.base.url", os.environ["BASE_URL"])  # noqa: F821
    env["ir.config_parameter"].sudo().set_param("web.base.url.freeze", "True")  # noqa: F821
    env.cr.commit()  # noqa: F821
    print(f"admin login: {admin.login}")
    print(f"admin password: {password}")

elif phase == "finalize":
    if not company.chart_template:
        env["account.chart.template"].try_loading("ke", company=company, install_demo=False)  # noqa: F821
    if company.account_price_include != "tax_included":
        # Kenyan shelf prices include VAT. Only possible before the first entry.
        company.account_price_include = "tax_included"
    company.write(
        {
            "ke_etims_enabled": True,
            "ke_etims_connector": "demo",
            "ke_etims_environment": "sandbox",
            "ke_etims_branch_code": "00",
        }
    )
    if "account.cash.rounding" in env:  # noqa: F821
        env["account.cash.rounding"]._ke_setup_kes_rounding(company)  # noqa: F821
    rounding = env.ref("ke_pos.cash_rounding_kes_1", raise_if_not_found=False)  # noqa: F821
    for config in env["pos.config"].search([("company_id", "=", company.id)]):  # noqa: F821
        if rounding:
            config.write({"cash_rounding": True, "rounding_method": rounding.id, "only_round_cash_method": False})
    env["ir.config_parameter"].sudo().set_param("web.base.url", os.environ["BASE_URL"])  # noqa: F821
    env.cr.commit()  # noqa: F821
    print("Chart of accounts:", company.chart_template, "| eTIMS: training mode (switch to a real connector before go-live)")

else:
    raise SystemExit(f"Unknown SETUP_PHASE {phase!r}")
