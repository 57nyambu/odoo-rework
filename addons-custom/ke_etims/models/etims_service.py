from odoo import models
from odoo.exceptions import UserError


class KeEtimsService(models.AbstractModel):
    """Interface every eTIMS connector implementation must provide.

    Swap the connector (bought, Enterprise, or custom-built) by installing
    a module that depends on this one and overrides `sign_and_submit`.
    Nothing outside this module should ever call a connector's API directly
    — that's what makes the vendor choice reversible later.
    """

    _name = "ke.etims.service"
    _description = "Kenya eTIMS submission interface"

    def sign_and_submit(self, invoice):
        """Sign and submit an account.move to KRA via eTIMS.

        :param invoice: a single posted account.move record
        :return: dict with keys: kra_invoice_number, qr_code, raw_response
        """
        raise UserError(
            "No eTIMS connector is configured. Install and configure a "
            "connector module that overrides ke.etims.service.sign_and_submit "
            "(see README.md, eTIMS section)."
        )
