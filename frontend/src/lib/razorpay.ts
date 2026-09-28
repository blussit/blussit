import axios from "axios";
import { paymentApi, type PaymentStatusResult, type VerifyPaymentResult } from "../api/payment";
import { getErrorMessage } from "./api-client";

/**
 * Razorpay Standard Web Checkout, end to end: create the server-side
 * order, open the modal, and verify the signature on our backend. The
 * caller gets ONE promise:
 *   - resolved with the verify result once the backend confirms the payment;
 *   - rejected with PaymentCancelled when the customer closes the modal
 *     without paying (PaymentFailed, a subclass, when an attempt failed
 *     first — it carries the bank's reason);
 *   - rejected with PaymentPendingConfirmation when the money went through
 *     at Razorpay but our server couldn't confirm it within ~60 s. That is
 *     NOT a failure: the server-side reconciliation finishes it on its own,
 *     so the customer must never be told it failed or to pay again;
 *   - rejected with the API error when the server gives a real answer
 *     (e.g. a forged signature, or a payment it had to park for review).
 *
 * Two shapes come back from create-order and checkout takes whichever it
 * is handed: an `order_id` (pay once) or a `subscription_id` (authorise a
 * recurring auto-pay mandate). They are signed over DIFFERENT messages,
 * so the handler passes back exactly the id it was given and the backend
 * picks the matching check — never both.
 */

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void; on: (event: string, cb: (resp: unknown) => void) => void };
  }
}

export class PaymentCancelled extends Error {
  constructor(message = "Payment cancelled") {
    super(message);
    this.name = "PaymentCancelled";
  }
}

/** Closed after a failed attempt. Extends PaymentCancelled so callers that
 *  treat any close as "nothing happened" still do. */
export class PaymentFailed extends PaymentCancelled {
  readonly reason: string;

  constructor(reason: string) {
    super(reason);
    this.reason = reason;
    this.name = "PaymentFailed";
  }
}

export class PaymentPendingConfirmation extends Error {
  constructor() {
    super("We're still confirming your payment. If money left your account it will be applied automatically within a few minutes — please don't pay again.");
    this.name = "PaymentPendingConfirmation";
  }
}

/** Money received but parked for a human (e.g. the booking was cancelled meanwhile). */
export class PaymentNeedsAttention extends Error {
  constructor() {
    super("We received your payment but couldn't apply it automatically. Our team will fix or refund it — please don't pay again.");
    this.name = "PaymentNeedsAttention";
  }
}

export interface PaymentHooks {
  /** Paid at Razorpay; confirming with our server is taking a retry. */
  onConfirming?: () => void;
  /** An attempt failed inside the checkout (it stays open for a retry). */
  onFailed?: (reason: string) => void;
}

/** Bank reasons don't always end in a full stop; ours get glued after them. */
export function sentence(text: string): string {
  const t = text.trim();
  return /[.!?]$/.test(t) ? t : `${t}.`;
}

/** The message to show for anything payWithRazorpay rejects with. */
export function paymentErrorMessage(err: unknown): string {
  if (err instanceof PaymentFailed || err instanceof PaymentPendingConfirmation || err instanceof PaymentNeedsAttention) return err.message;
  return getErrorMessage(err);
}

const CONFIRM_WINDOW_MS = 60_000;
const DISMISS_CHECK_MS = 8_000;

let checkoutScript: Promise<void> | null = null;

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  if (!checkoutScript) {
    checkoutScript = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        checkoutScript = null; // allow a retry after a network blip
        reject(new Error("Couldn't load the payment window — check your connection and try again."));
      };
      document.body.appendChild(script);
    });
  }
  return checkoutScript;
}

/** No answer, or the server/proxy failed — as opposed to a real 4xx verdict. */
function isTransient(err: unknown): boolean {
  if (!axios.isAxiosError(err)) return false;
  const status = err.response?.status;
  return !status || status >= 500 || status === 408 || status === 429 || status === 401;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function asVerifyResult(s: PaymentStatusResult): VerifyPaymentResult {
  return {
    status: "paid",
    purpose: s.purpose,
    auto_pay: s.auto_pay,
    booking_id: s.booking_id,
    booking_group_id: s.booking_group_id,
    subscription_id: s.subscription_id,
    subscription: s.subscription,
    confirmation_token: s.confirmation_token,
    already_processed: true,
  };
}

type Reference = { order_id?: string; subscription_id?: string };
type VerifyPayload = Parameters<typeof paymentApi.verify>[0];

/** After a successful payment whose verify didn't get an answer: retry the
 *  (idempotent) verify, and ask the server — which asks Razorpay — for the
 *  order's state, until one of them confirms or the window runs out. */
async function confirmPayment(payload: VerifyPayload, ref: Reference): Promise<VerifyPaymentResult> {
  const deadline = Date.now() + CONFIRM_WINDOW_MS;
  let delay = 2000;
  while (Date.now() < deadline) {
    await sleep(delay);
    try {
      return await paymentApi.verify(payload);
    } catch (err) {
      if (!isTransient(err)) throw err;
    }
    try {
      const s = await paymentApi.status(ref);
      if (s.status === "paid") return asVerifyResult(s);
      if (s.status === "needs_attention") throw new PaymentNeedsAttention();
    } catch (err) {
      if (err instanceof PaymentNeedsAttention) throw err;
    }
    delay = Math.min(delay * 1.5, 6000);
  }
  throw new PaymentPendingConfirmation();
}

/** One look when the modal closes: a UPI app or bank page can finish the
 *  payment after the customer has given up waiting in the checkout. */
async function paidAnyway(ref: Reference): Promise<PaymentStatusResult | null> {
  try {
    const s = await Promise.race([paymentApi.status(ref), sleep(DISMISS_CHECK_MS).then(() => null)]);
    return s && (s.status === "paid" || s.status === "needs_attention") ? s : null;
  } catch {
    return null;
  }
}

export async function payWithRazorpay(
  order: { purpose: "booking" | "booking_group" | "subscription"; booking_id?: string; booking_group_id?: string; plan_id?: string; vehicle_id?: string; service_id?: string; vehicle_type?: string; auto_pay?: boolean },
  prefill?: { name?: string | null; email?: string | null; contact?: string | null },
  /** Told what actually got created — auto-pay can silently degrade to a
   *  one-time purchase when the gateway won't set a mandate up. */
  onCreated?: (created: { auto_pay?: boolean; auto_pay_unavailable?: boolean; amount: number }) => void,
  hooks?: PaymentHooks
): Promise<VerifyPaymentResult> {
  await loadCheckout();
  const created = await paymentApi.createOrder(order);
  onCreated?.(created);
  // Impossible to confuse a sandbox payment with a real one: the checkout
  // itself is labelled, and the console says so for anyone watching.
  if (created.mode === "test") {
    console.warn("Razorpay is in TEST mode — no real money will move.");
  }
  const ref: Reference = created.subscription_id ? { subscription_id: created.subscription_id } : { order_id: created.order_id };

  return new Promise<VerifyPaymentResult>((resolve, reject) => {
    let lastFailure: string | null = null;
    let paid = false;
    const rzp = new window.Razorpay!({
      key: created.key_id,
      amount: created.amount,
      currency: created.currency,
      ...(created.subscription_id ? { subscription_id: created.subscription_id } : { order_id: created.order_id }),
      name: created.mode === "test" ? "BLUSSIT (TEST MODE)" : "BLUSSIT",
      description: created.mode === "test" ? `TEST — ${created.description}` : created.description,
      theme: { color: "#E8A900" },
      prefill: {
        name: prefill?.name || undefined,
        email: prefill?.email || undefined,
        contact: prefill?.contact || undefined,
      },
      // Success path: hand the ids to OUR backend — nothing is considered
      // paid until the HMAC signature checks out there. Razorpay returns
      // razorpay_subscription_id for a mandate and razorpay_order_id for a
      // one-time order; forward whichever we opened with, so a tampered
      // response can't pick the weaker check.
      handler: async (resp: { razorpay_order_id?: string; razorpay_subscription_id?: string; razorpay_payment_id: string; razorpay_signature: string }) => {
        paid = true;
        const payload: VerifyPayload = {
          ...(created.subscription_id
            ? { razorpay_subscription_id: created.subscription_id }
            : { razorpay_order_id: created.order_id }),
          razorpay_payment_id: resp.razorpay_payment_id,
          razorpay_signature: resp.razorpay_signature,
        };
        try {
          resolve(await paymentApi.verify(payload));
        } catch (err) {
          if (!isTransient(err)) {
            reject(err);
            return;
          }
          // The money moved; only our answer got lost. Never report that
          // as a failure.
          hooks?.onConfirming?.();
          confirmPayment(payload, ref).then(resolve, reject);
        }
      },
      modal: {
        ondismiss: () => {
          if (paid) return;
          paidAnyway(ref).then((s) => {
            if (s?.status === "paid") resolve(asVerifyResult(s));
            else if (s?.status === "needs_attention") reject(new PaymentNeedsAttention());
            else reject(lastFailure ? new PaymentFailed(lastFailure) : new PaymentCancelled());
          });
        },
      },
    });
    // A failed attempt keeps the modal open for a retry on the same order.
    // The reason is shown to the customer and recorded server-side (so the
    // booking page can say why) — it never changes what's paid.
    rzp.on("payment.failed", (resp) => {
      const error = ((resp as { error?: Record<string, unknown> })?.error || {}) as {
        code?: string; description?: string; reason?: string; step?: string; source?: string; metadata?: { payment_id?: string };
      };
      lastFailure = sentence(error.description || "The payment didn't go through");
      hooks?.onFailed?.(lastFailure);
      paymentApi
        .reportFailure({
          ...(created.subscription_id ? { razorpay_subscription_id: created.subscription_id } : { razorpay_order_id: created.order_id }),
          razorpay_payment_id: error.metadata?.payment_id,
          code: error.code,
          description: error.description?.slice(0, 300),
          reason: error.reason,
          step: error.step,
          source: error.source,
        })
        .catch(() => undefined);
    });
    rzp.open();
  });
}
