import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";
import { logPosMessage } from "@point_of_sale/app/utils/pretty_console_log";

patch(OrderPaymentValidation.prototype, {
    async beforePostPushOrderResolve(order, orderServerIds) {
        if (order.keEtimsSignBeforePrint && orderServerIds.length) {
            try {
                // Signs with eTIMS and reloads the order (CU invoice number,
                // signature, QR data) so the receipt can print them.
                await this.pos.data.callRelated(
                    "pos.order",
                    "ke_etims_sign_pos_orders",
                    [orderServerIds],
                    {},
                    false
                );
            } catch (error) {
                // KRA or network unavailable: the receipt says "eTIMS pending"
                // and the server keeps retrying in the background.
                logPosMessage("ke_pos", "eTIMS", "Signing deferred", "#e67e22", [error]);
            }
        }
        return super.beforePostPushOrderResolve(...arguments);
    },
});
