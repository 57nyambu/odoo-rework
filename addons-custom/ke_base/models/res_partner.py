import re

from odoo import api, models

# Matches Kenyan mobile numbers with an optional 254 or 0 prefix, followed
# by a 9-digit subscriber number starting with 7 or 1 (Safaricom, Airtel,
# Telkom). Adjust if you need to support other prefixes.
KE_MOBILE_RE = re.compile(r"^(?:254|0)?(7\d{8}|1\d{8})$")


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def ke_normalize_phone(self, phone):
        """Normalize a Kenyan mobile number to the 2547XXXXXXXX /
        2541XXXXXXXX format required by the Daraja API.

        Returns None if the input doesn't look like a Kenyan mobile number.
        """
        if not phone:
            return None
        digits = re.sub(r"[^\d]", "", phone)
        match = KE_MOBILE_RE.match(digits)
        if not match:
            return None
        return f"254{match.group(1)}"
