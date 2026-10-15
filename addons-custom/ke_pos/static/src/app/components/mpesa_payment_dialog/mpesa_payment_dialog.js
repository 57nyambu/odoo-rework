import { Component, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { formatCurrency } from "@web/core/currency";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";

// Same rules as ke_base/tools/ke_utils.py
const KE_MOBILE_RE = /^(?:254|0)?([71]\d{8})$/;
const MPESA_CODE_RE = /^[A-Z0-9]{10}$/;
const POLL_INTERVAL_MS = 3000;
const MAX_WAIT_MS = 120000;
const FINAL_FAILURE_STATES = ["failed", "cancelled", "expired"];

/**
 * Collects an M-Pesa payment for one payment line, either by
 *  - sending an STK push to the customer's phone and waiting for Safaricom's
 *    confirmation, or
 *  - verifying the M-Pesa code of a payment the customer made straight to the
 *    till / paybill.
 *
 * Resolves (through `getPayload`) with `{ transaction }` once Safaricom has
 * confirmed the payment; closing the dialog resolves with nothing.
 */
export class MpesaPaymentDialog extends Component {
    static template = "ke_pos.MpesaPaymentDialog";
    static components = { Dialog };
    static props = {
        paymentMethod: Object,
        paymentLine: Object,
        order: Object,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.pos = usePos();
        const method = this.props.paymentMethod;
        this.state = useState({
            mode: method.ke_mpesa_allow_stk ? "stk" : "code",
            phone: this.props.order.partner_id?.phone || "",
            code: "",
            amount: Math.round(this.props.paymentLine.getAmount()),
            status: "idle", // idle | busy | waiting | error
            message: "",
            transaction: null,
        });
        this.pollTimer = null;
        this.startedAt = 0;
        onWillUnmount(() => this.stopPolling());
    }

    get method() {
        return this.props.paymentMethod;
    }

    get showModeSwitch() {
        return this.method.ke_mpesa_allow_stk && this.method.ke_mpesa_allow_code;
    }

    get formattedAmount() {
        return formatCurrency(this.state.amount || 0, this.props.order.currency.id);
    }

    get primaryDisabled() {
        return (
            this.state.status === "busy" ||
            (this.state.mode === "stk" && this.state.status === "waiting")
        );
    }

    get alertClass() {
        return {
            busy: "alert-info",
            waiting: "alert-info",
            error: "alert-danger",
        }[this.state.status] || "alert-secondary";
    }

    setMode(mode) {
        if (mode === this.state.mode) {
            return;
        }
        this.stopPolling();
        this.state.mode = mode;
        this.state.status = "idle";
        this.state.message = "";
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !this.primaryDisabled) {
            ev.preventDefault();
            this.confirm();
        }
    }

    confirm() {
        return this.state.mode === "stk" ? this.sendStkPush() : this.verifyCode();
    }

    cancel() {
        this.stopPolling();
        this.props.close();
    }

    // ------------------------------------------------------------------
    // STK push
    // ------------------------------------------------------------------
    normalizedPhone() {
        const digits = (this.state.phone || "").replace(/\D/g, "");
        const match = digits.match(KE_MOBILE_RE);
        return match ? `254${match[1]}` : null;
    }

    validAmount() {
        const amount = Number(this.state.amount);
        if (!Number.isInteger(amount) || amount < 1) {
            this.fail(_t("M-Pesa only accepts whole shillings (KES 1 or more)."));
            return null;
        }
        return amount;
    }

    async sendStkPush() {
        const phone = this.normalizedPhone();
        if (!phone) {
            return this.fail(_t("Enter the customer's Safaricom number, e.g. 0712 345 678."));
        }
        const amount = this.validAmount();
        if (!amount) {
            return;
        }
        this.state.status = "busy";
        this.state.message = _t("Sending the payment request to M-Pesa...");
        try {
            const order = this.props.order;
            const transaction = await this.pos.data.call(
                "pos.payment.method",
                "ke_mpesa_pos_stk_push",
                [[this.method.id], phone, amount, order.uuid, order.pos_reference || order.name],
                {},
                false
            );
            this.state.transaction = transaction;
            this.state.status = "waiting";
            this.state.message = _t(
                "Request sent to %s. Ask the customer to enter their M-Pesa PIN.",
                transaction.phone_masked
            );
            this.startPolling();
        } catch (error) {
            this.fail(this.errorMessage(error));
        }
    }

    startPolling() {
        this.stopPolling();
        this.startedAt = Date.now();
        this.schedulePoll();
    }

    schedulePoll() {
        this.pollTimer = setTimeout(() => this.poll(), POLL_INTERVAL_MS);
    }

    stopPolling() {
        clearTimeout(this.pollTimer);
        this.pollTimer = null;
    }

    async poll() {
        const current = this.state.transaction;
        if (!current || this.state.status !== "waiting") {
            return;
        }
        try {
            const transaction = await this.pos.data.call(
                "pos.payment.method",
                "ke_mpesa_pos_check_status",
                [[this.method.id], current.id],
                {},
                false
            );
            this.state.transaction = transaction;
            if (transaction.state === "confirmed") {
                return this.succeed(transaction);
            }
            if (FINAL_FAILURE_STATES.includes(transaction.state)) {
                return this.fail(transaction.message || _t("The M-Pesa payment was not completed."));
            }
        } catch {
            // Network hiccup: keep waiting, Safaricom's answer is stored server side.
        }
        if (Date.now() - this.startedAt > MAX_WAIT_MS) {
            return this.fail(
                _t(
                    "No confirmation from M-Pesa yet. If the customer was charged, choose " +
                        "'Enter M-Pesa code' and type the code from their SMS."
                )
            );
        }
        this.schedulePoll();
    }

    // ------------------------------------------------------------------
    // Code verification (customer paid to the till / paybill directly)
    // ------------------------------------------------------------------
    async verifyCode() {
        const code = (this.state.code || "").replace(/\s/g, "").toUpperCase();
        if (!MPESA_CODE_RE.test(code)) {
            return this.fail(_t("An M-Pesa code has 10 letters and digits, e.g. SJK3ABCD9Z."));
        }
        this.state.status = "busy";
        this.state.message = _t("Checking %s with M-Pesa...", code);
        try {
            const transaction = await this.pos.data.call(
                "pos.payment.method",
                "ke_mpesa_pos_claim_code",
                [[this.method.id], code, this.props.order.uuid],
                {},
                false
            );
            this.succeed(transaction);
        } catch (error) {
            this.fail(this.errorMessage(error));
        }
    }

    // ------------------------------------------------------------------
    succeed(transaction) {
        this.stopPolling();
        this.props.getPayload({ transaction });
        this.props.close();
    }

    fail(message) {
        this.stopPolling();
        this.state.status = "error";
        this.state.message = message;
    }

    errorMessage(error) {
        return (
            error?.data?.message ||
            error?.message ||
            _t("Could not reach the server. Check the internet connection and try again.")
        );
    }
}
