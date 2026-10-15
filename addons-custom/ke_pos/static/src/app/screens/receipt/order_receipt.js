import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { generateQRCodeDataUrl } from "@point_of_sale/utils";
import { patch } from "@web/core/utils/patch";

patch(OrderReceipt.prototype, {
    get keShowEtims() {
        return Boolean(this.order.company.ke_etims_enabled && this.order.finalized);
    },

    get keEtimsQrCode() {
        const data = this.order.ke_etims_qr_data;
        return data ? generateQRCodeDataUrl(data, { width: 120, height: 120 }) : false;
    },

    get keEtimsPending() {
        return ["to_send", "error"].includes(this.order.ke_etims_state) || !this.order.ke_etims_invoice_number;
    },
});
