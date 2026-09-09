import { paymentApi, type VerifyPaymentResult } from "../api/payment";

/**
 * Razorpay Standard Web Checkout, end to end: create the server-side
 * order, open the modal, and verify the signature on our backend. The
 * caller gets ONE promise — resolved with the verify result only after
 * the backend confirms the signature, rejected with PaymentCancelled
 * when the customer closes the modal, and rejected with the API error
 * when verification fails.
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
  order: { purpose: "booking" | "subscription"; booking_id?: string; plan_id?: string; vehicle_type?: string },
  prefill?: { name?: string | null; email?: string | null; contact?: string | null }
): Promise<VerifyPaymentResult> {
  await loadCheckout();
  const created = await paymentApi.createOrder(order);

  return new Promise<VerifyPaymentResult>((resolve, reject) => {
    const rzp = new window.Razorpay!({
      key: created.key_id,
      amount: created.amount,
      currency: created.currency,
      order_id: created.order_id,
      name: "BLUSSIT",
      description: created.description,
      theme: { color: "#E8A900" },
      prefill: {
        name: prefill?.name || undefined,
        email: prefill?.email || undefined,
        contact: prefill?.contact || undefined,
      },
      // Success path: hand the three ids to OUR backend — nothing is
      // considered paid until the HMAC signature checks out there.
      handler: async (resp: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) => {
        try {
          resolve(await paymentApi.verify(resp));
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
