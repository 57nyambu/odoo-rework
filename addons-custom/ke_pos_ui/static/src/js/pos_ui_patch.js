/** @odoo-module */

// CAUTION: same caveat as payment_mpesa.js — confirm ProductScreen's
// current import path and method name against your installed
// point_of_sale addon source before relying on this. This demonstrates
// the patch() pattern for extending POS checkout UX without forking core;
// replace the body with your actual customization.

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

patch(ProductScreen.prototype, {
    async addProductToOrder(product) {
        await super.addProductToOrder(...arguments);
        console.debug("ke_pos_ui: product added", product.display_name);
    },
});
