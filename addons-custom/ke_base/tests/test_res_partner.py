from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "ke")
class TestResPartnerKe(TransactionCase):
    def test_kra_pin_normalized_and_validated(self):
        kenya = self.env.ref("base.ke")
        partner = self.env["res.partner"].create(
            {"name": "Duka Ltd", "country_id": kenya.id, "vat": "p051 234 567x", "is_company": True}
        )
        self.assertEqual(partner.vat, "P051234567X")
        self.assertEqual(partner.ke_taxpayer_type, "non_individual")
        with self.assertRaises(ValidationError):
            partner.write({"vat": "12345"})

    def test_foreign_tax_id_untouched(self):
        partner = self.env["res.partner"].create(
            {"name": "Acme", "country_id": self.env.ref("base.us").id, "vat": "12-3456789"}
        )
        self.assertEqual(partner.vat, "12-3456789")

    def test_kenya_vat_label(self):
        self.assertEqual(self.env.ref("base.ke").vat_label, "KRA PIN")
