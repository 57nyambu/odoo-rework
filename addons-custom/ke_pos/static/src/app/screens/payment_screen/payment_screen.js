import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    async sendForceDone(line) {
        if (
            line.payment_method_id.use_payment_terminal === "ke_mpesa" &&
            !this.pos.config.ke_mpesa_allow_force_done
        ) {
            this.dialog.add(AlertDialog, {
                title: _t("M-Pesa"),
                body: _t(
                    "An M-Pesa payment cannot be accepted without Safaricom's confirmation. " +
                        "Press Retry and use 'Enter M-Pesa code' with the code from the customer's SMS."
                ),
            });
            return;
        }
        return super.sendForceDone(...arguments);
    },
});
