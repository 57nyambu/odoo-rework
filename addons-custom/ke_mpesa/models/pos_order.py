from odoo import api, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def ke_mpesa_initiate_stk_push(self, payment_method_id, phone, amount, reference):
        """Called from the POS frontend (payment_mpesa.js) *before* the
        order is finalized server-side — `reference` is a client-generated
        POS order reference (e.g. the frontend order's uid), not a real
        pos.order id yet, since POS orders are only persisted on sync.

        Normalizes the phone number, creates the pending mpesa.transaction
        record, then calls Daraja. Returns the CheckoutRequestID the
        frontend polls on. Link mpesa.transaction.pos_order_id afterwards,
        when the real order syncs, by matching on checkout_request_id.
        """
        payment_method = self.env["pos.payment.method"].browse(payment_method_id)
        normalized_phone = self.env["res.partner"].ke_normalize_phone(phone)
        if not normalized_phone:
            raise ValueError(f"'{phone}' doesn't look like a Kenyan mobile number.")

        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        callback_url = f"{base_url}/ke_mpesa/callback"

        response = self.env["ke.mpesa.daraja"].stk_push(
            payment_method,
            normalized_phone,
            amount,
            account_reference=reference or "POS",
            callback_url=callback_url,
        )

        checkout_request_id = response.get("CheckoutRequestID")
        self.env["mpesa.transaction"].create(
            {
                "phone": normalized_phone,
                "amount": amount,
                "checkout_request_id": checkout_request_id,
                "merchant_request_id": response.get("MerchantRequestID"),
                "state": "pending",
            }
        )
        return {"checkout_request_id": checkout_request_id}
