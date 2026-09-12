import { paymentApi, type VerifyPaymentResult } from "../api/payment";

/**
 * Razorpay Standard Web Checkout, end to end: create the server-side
 * order, open the modal, and verify the signature on our backend. The
 * caller gets ONE promise — resolved with the verify result only after
 * the backend confirms the signature, rejected with PaymentCancelled
 * when the customer closes the modal, and rejected with the API error
 * when verification fails.
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
  constructor() {
    super("Payment cancelled");
    this.name = "PaymentCancelled";
  }
}

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

export async function payWithRazorpay(
  order: { purpose: "booking" | "booking_group" | "subscription"; booking_id?: string; booking_group_id?: string; plan_id?: string; vehicle_id?: string; service_id?: string; vehicle_type?: string; auto_pay?: boolean },
  prefill?: { name?: string | null; email?: string | null; contact?: string | null },
  /** Told what actually got created — auto-pay can silently degrade to a
   *  one-time purchase when the gateway won't set a mandate up. */
  onCreated?: (created: { auto_pay?: boolean; auto_pay_unavailable?: boolean; amount: number }) => void
): Promise<VerifyPaymentResult> {
  await loadCheckout();
  const created = await paymentApi.createOrder(order);
  onCreated?.(created);
  // Impossible to confuse a sandbox payment with a real one: the checkout
  // itself is labelled, and the console says so for anyone watching.
  if (created.mode === "test") {
    console.warn("Razorpay is in TEST mode — no real money will move.");
  }

  return new Promise<VerifyPaymentResult>((resolve, reject) => {
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
        try {
          resolve(
            await paymentApi.verify({
              ...(created.subscription_id
                ? { razorpay_subscription_id: created.subscription_id }
                : { razorpay_order_id: created.order_id }),
              razorpay_payment_id: resp.razorpay_payment_id,
              razorpay_signature: resp.razorpay_signature,
            })
          );
        } catch (err) {
          reject(err);
        }
      },
      modal: { ondismiss: () => reject(new PaymentCancelled()) },
    });
    // A failed attempt keeps the modal open for retry — Razorpay shows its
    // own error there; nothing for us to do until dismiss or success.
    rzp.on("payment.failed", () => {});
    rzp.open();
  });
}
