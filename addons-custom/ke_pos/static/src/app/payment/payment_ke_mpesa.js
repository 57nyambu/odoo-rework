import { _t } from "@web/core/l10n/translation";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { MpesaPaymentDialog } from "@ke_pos/app/components/mpesa_payment_dialog/mpesa_payment_dialog";

/**
 * M-Pesa "terminal": registered for payment methods whose
 * `use_payment_terminal` is `ke_mpesa`. Selecting the method opens the
 * M-Pesa dialog; the payment line is only marked done once Safaricom has
 * confirmed the money (STK callback / status query, or a verified C2B code).
 */
export class PaymentKeMpesa extends PaymentInterface {
    async sendPaymentRequest(uuid) {
        super.sendPaymentRequest(...arguments);
        const order = this.pos.getOrder();
        const line = order.payment_ids.find((payment) => payment.uuid === uuid);
        if (!line) {
            return false;
        }
        const result = await makeAwaitable(this.env.services.dialog, MpesaPaymentDialog, {
            paymentMethod: this.payment_method_id,
            paymentLine: line,
            order,
        });
        const transaction = result?.transaction;
        if (!transaction) {
            return false;
        }
        line.setAmount(transaction.amount);
        // Standard pos.payment fields: the M-Pesa code is the transaction ID
        // printed on the receipt; the STK checkout ID links back to Daraja.
        line.transaction_id = transaction.receipt_number || false;
        line.payment_ref_no = transaction.checkout_request_id || transaction.receipt_number;
        line.setReceiptInfo(this._receiptInfo(transaction));
        this.pos.notification.add(
            _t("M-Pesa payment received: %s", transaction.receipt_number || transaction.phone_masked),
            { type: "success" }
        );
        return true;
    }

    async sendPaymentCancel() {
        // An STK prompt cannot be withdrawn from the customer's phone. If the
        // customer still pays, the payment shows as unclaimed and can be
        // used with "Enter M-Pesa code".
        return true;
    }

    _receiptInfo(transaction) {
        const lines = [_t("M-PESA")];
        if (transaction.receipt_number) {
            lines.push(_t("Code: %s", transaction.receipt_number));
        }
        if (transaction.phone_masked) {
            lines.push(_t("Phone: %s", transaction.phone_masked));
        }
        return lines.join("\n");
    }
}

register_payment_method("ke_mpesa", PaymentKeMpesa);
