from odoo.tests import BaseCase, tagged

from odoo.addons.ke_base.tools import (
    format_ke_phone,
    is_valid_kra_pin,
    is_valid_mpesa_code,
    kra_pin_taxpayer_type,
    mask_ke_phone,
    normalize_ke_msisdn,
    normalize_kra_pin,
)


@tagged("post_install", "-at_install", "ke")
class TestKeUtils(BaseCase):
    def test_normalize_msisdn(self):
        for raw in ("0712345678", "712345678", "254712345678", "+254 712 345 678", "(+254) 712-345-678"):
            self.assertEqual(normalize_ke_msisdn(raw), "254712345678", raw)
        self.assertEqual(normalize_ke_msisdn("0110123456"), "254110123456")
        for bad in (None, "", "0812345678", "12345", "+255712345678", "07123456789"):
            self.assertIsNone(normalize_ke_msisdn(bad), bad)

    def test_format_and_mask(self):
        self.assertEqual(format_ke_phone("254712345678"), "0712 345 678")
        self.assertEqual(mask_ke_phone("+254712345678"), "0712***678")
        self.assertEqual(format_ke_phone("not a phone"), "not a phone")

    def test_kra_pin(self):
        self.assertTrue(is_valid_kra_pin("P051234567X"))
        self.assertTrue(is_valid_kra_pin(" a012345678z "))
        self.assertFalse(is_valid_kra_pin("B051234567X"))
        self.assertFalse(is_valid_kra_pin("P05123456X"))
        self.assertEqual(normalize_kra_pin("p051-234-567x"), "P051234567X")
        self.assertEqual(kra_pin_taxpayer_type("A012345678Z"), "individual")
        self.assertEqual(kra_pin_taxpayer_type("P051234567X"), "non_individual")

    def test_mpesa_code(self):
        self.assertTrue(is_valid_mpesa_code("sjk3abcd9z"))
        self.assertFalse(is_valid_mpesa_code("SJK3ABCD9"))
