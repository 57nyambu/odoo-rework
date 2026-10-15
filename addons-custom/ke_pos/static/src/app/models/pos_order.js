import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    /**
     * With eTIMS enabled, wait after the order is saved so it can be signed
     * by KRA before the receipt is printed (see order_payment_validation.js).
     */
    waitForPushOrder() {
        return this.keEtimsSignBeforePrint || super.waitForPushOrder(...arguments);
    },

    get keEtimsSignBeforePrint() {
        return Boolean(this.company?.ke_etims_enabled && this.config?.ke_etims_wait_for_signature);
    },
});
