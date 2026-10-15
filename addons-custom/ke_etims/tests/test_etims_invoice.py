from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "ke")
class TestKeEtimsInvoice(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("ke")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.write(
            {
                "vat": "P051234567X",
                "ke_etims_enabled": True,
                "ke_etims_connector": "demo",
                "ke_etims_environment": "sandbox",
                "ke_etims_branch_code": "00",
            }
        )
        ChartTemplate = cls.env["account.chart.template"].with_company(cls.company)
        cls.vat16 = ChartTemplate.ref("ST16")
        cls.vat8 = ChartTemplate.ref("ST8")
        cls.vat0 = ChartTemplate.ref("ST0")
        cls.vat_exempt = ChartTemplate.ref("STEX")
        cls.wht2 = ChartTemplate.ref("SWT2")
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "Naivas Test Ltd",
                "country_id": cls.env.ref("base.ke").id,
                "vat": "P000111222Q",
                "is_company": True,
            }
        )

    def _invoice(self, taxes, price=1000.0, post=True, move_type="out_invoice"):
        move = self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.customer.id,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "quantity": 2,
                            "price_unit": price,
                            "tax_ids": [Command.set(taxes.ids)],
                        }
                    )
                ],
            }
        )
        if post:
            move.action_post()
        return move

    def test_tax_type_mapping(self):
        self.assertEqual(self.vat16.ke_etims_tax_type, "B")
        self.assertEqual(self.vat8.ke_etims_tax_type, "E")
        self.assertEqual(self.vat0.ke_etims_tax_type, "C")
        self.assertEqual(self.vat_exempt.ke_etims_tax_type, "A")
        self.assertFalse(self.wht2.ke_etims_tax_type)
        Tax = self.env["account.tax"]
        self.assertEqual(Tax._ke_etims_tax_type_for(Tax), "D")

    def test_invoice_queued_then_signed(self):
        move = self._invoice(self.vat16)
        self.assertEqual(move.ke_etims_state, "to_send")
        sent, failed = move._ke_etims_send()
        self.assertEqual(sent, move)
        self.assertFalse(failed)
        self.assertEqual(move.ke_etims_state, "sent")
        self.assertTrue(move.ke_etims_invoice_number.startswith("TRAIN"))
        self.assertTrue(move.ke_etims_training)
        self.assertTrue(move._ke_etims_qr_code_data_uri().startswith("data:image/png;base64,"))

    def test_payload_totals_by_tax_type(self):
        move = self._invoice(self.vat16)
        payload = move._ke_etims_prepare_payload()
        self.assertEqual(payload["document_type"], "sale")
        self.assertEqual(payload["seller"]["pin"], "P051234567X")
        self.assertEqual(payload["buyer"]["pin"], "P000111222Q")
        self.assertEqual(payload["currency"], move.currency_id.name)
        line = payload["lines"][0]
        self.assertEqual(line["tax_type"], "B")
        self.assertAlmostEqual(payload["totals"]["taxable_B"], 2000.0)
        self.assertAlmostEqual(payload["totals"]["tax_B"], 320.0)
        self.assertAlmostEqual(payload["totals"]["total_amount"], 2320.0)
        self.assertAlmostEqual(payload["totals"]["tax_A"], 0.0)

    def test_signed_invoice_cannot_be_reset(self):
        move = self._invoice(self.vat16)
        move._ke_etims_send()
        with self.assertRaises(UserError):
            move.button_draft()

    def test_unsent_invoice_reset_clears_queue(self):
        move = self._invoice(self.vat16)
        move.button_draft()
        self.assertEqual(move.ke_etims_state, "not_applicable")

    def test_connector_failure_is_recorded_not_raised(self):
        move = self._invoice(self.vat16)
        with patch.object(
            type(self.env["ke.etims.service"]), "_submit_demo", side_effect=UserError("KRA is down")
        ):
            sent, failed = move._ke_etims_send()
        self.assertEqual(failed, move)
        self.assertEqual(move.state, "posted")
        self.assertEqual(move.ke_etims_state, "error")
        self.assertIn("KRA is down", move.ke_etims_error)
        self.assertEqual(move.ke_etims_attempts, 1)
        # recovers on the next attempt
        move._ke_etims_send()
        self.assertEqual(move.ke_etims_state, "sent")

    def test_credit_note_references_original(self):
        move = self._invoice(self.vat16)
        move._ke_etims_send()
        reversal = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=move.ids
        ).create({"reason": "Damaged goods", "journal_id": move.journal_id.id})
        refund = self.env["account.move"].browse(reversal.refund_moves()["res_id"])
        refund.action_post()
        payload = refund._ke_etims_prepare_payload()
        self.assertEqual(payload["document_type"], "credit_note")
        self.assertEqual(payload["original_reference"], move.ke_etims_receipt_number)

    def test_disabled_company_not_applicable(self):
        self.company.ke_etims_enabled = False
        move = self._invoice(self.vat16)
        self.assertEqual(move.ke_etims_state, "not_applicable")

    def test_training_refused_in_production(self):
        with self.assertRaises(ValidationError):
            self.company.ke_etims_environment = "production"
