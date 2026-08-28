/** @odoo-module */

// CAUTION: POS frontend internals (OWL component/class names, import
// paths, PaymentInterface method signatures) shift between Odoo point
// releases more than any other part of the codebase. Before relying on
// this file, grep your installed point_of_sale addon source
// (odoo/addons/point_of_sale/static/src/app/) for the current
// PaymentInterface location and confirm send_payment_request's exact
// signature. This shows the pattern, not a guaranteed-current API.

import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

export class PaymentMpesa extends PaymentInterface {
    async send_payment_request(cid) {
        super.send_payment_request(cid);
        const line = this.pos.get_order().selected_paymentline;
        const phone = await this._kePromptPhone();
        if (!phone) {
            line.set_payment_status("retry");
            return false;
        }
        try {
            const result = await this.env.services.orm.call(
                "pos.order",
                "ke_mpesa_initiate_stk_push",
                [line.payment_method.id, phone, line.amount, this.pos.get_order().uid]
            );
            line.ke_checkout_request_id = result.checkout_request_id;
            line.set_payment_status("waiting");
            return this._kePollTransaction(line);
        } catch (error) {
            line.set_payment_status("retry");
            throw error;
        }
    }

    async _kePromptPhone() {
        // Replace with a proper OWL dialog component — window.prompt is a
        // placeholder so this file is complete and testable on day one.
        return window.prompt("Customer M-Pesa phone number (07XXXXXXXX):");
    }

    async _kePollTransaction(line) {
        for (let i = 0; i < 30; i++) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            const tx = await this.env.services.orm.call("mpesa.transaction", "search_read", [
                [["checkout_request_id", "=", line.ke_checkout_request_id]],
                ["state"],
            ]);
            if (tx.length && tx[0].state === "confirmed") {
                line.set_payment_status("done");
                return true;
            }
            if (tx.length && ["failed", "cancelled"].includes(tx[0].state)) {
                line.set_payment_status("retry");
                return false;
            }
        }
        // Timed out waiting for the Daraja callback — fall back to manual
        // confirmation or cash rather than blocking the sale indefinitely.
        line.set_payment_status("retry");
        return false;
    }
}

register_payment_method("mpesa_stk", PaymentMpesa);
