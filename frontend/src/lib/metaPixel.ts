/**
 * Meta Pixel conversion events (the pixel itself is set up in index.html).
 *
 * `fbq` only exists on the live site — index.html skips the pixel on
 * localhost and preview deploys so test bookings never reach Meta as real
 * sales — so every call here is a silent no-op there, and also when an ad
 * blocker removed it.
 *
 * Purchase means "a website booking became real" (cash at creation, online
 * once paid) or "a plan was bought". `eventId` is stable per booking/plan,
 * which lets Meta drop a repeat and later match a server-side copy.
 */

type Fbq = (command: "track", event: string, params?: Record<string, unknown>, options?: { eventID?: string }) => void;

function fbq(): Fbq | null {
  const f = (window as unknown as { fbq?: Fbq }).fbq;
  return typeof f === "function" ? f : null;
}

function track(event: string, params?: Record<string, unknown>, eventId?: string) {
  try {
    fbq()?.("track", event, params, eventId ? { eventID: eventId } : undefined);
  } catch {
    // tracking must never break a booking or a payment
  }
}

/** One id per visit (all its cars), so every path that confirms it agrees.
 *  The backend's payment-link page (payment_routes.py) builds the same. */
export function bookingEventId(groupId: string | null | undefined, bookingId: string) {
  return groupId ? `visit:${groupId}` : `booking:${bookingId}`;
}

/** A booking was confirmed, or a plan bought — the event ads optimise for. */
export function trackPurchase(args: { value: number; eventId: string; contentName?: string; contentType: "booking" | "plan"; numItems?: number }) {
  track(
    "Purchase",
    {
      value: Math.round(args.value * 100) / 100,
      currency: "INR",
      content_type: "product",
      content_category: args.contentType,
      content_name: args.contentName,
      num_items: args.numItems,
    },
    args.eventId
  );
}

/** The visitor picked what they want and moved on to address/slot (or to pay for a plan). */
export function trackInitiateCheckout(args: { value?: number; contentType: "booking" | "plan"; contentName?: string; numItems?: number }) {
  track("InitiateCheckout", {
    ...(args.value != null ? { value: Math.round(args.value * 100) / 100, currency: "INR" } : {}),
    content_category: args.contentType,
    content_name: args.contentName,
    num_items: args.numItems,
  });
}

/** Someone outside the service area left their number to hear when we arrive. */
export function trackLead(contentName: string) {
  track("Lead", { content_name: contentName });
}

/** Someone opened a booking/plan page — the top of the funnel in Ads Manager. */
export function trackViewContent(contentName: string) {
  track("ViewContent", { content_name: contentName });
}

/** Signed-in portals (customer app, staff) talking to us isn't ad traffic. */
const PORTAL_PATH = /^\/(app|admin|manager|captain)(\/|$)/;

/** Reached out: "whatsapp", "phone" or "contact form". */
export function trackContact(channel: string) {
  if (PORTAL_PATH.test(window.location.pathname)) return;
  track("Contact", { content_name: channel });
}

/** Every call / WhatsApp link on the public site counts as a Contact — one
 *  listener instead of an onClick on each link. Installed once (main.tsx). */
export function installContactTracking() {
  document.addEventListener(
    "click",
    (e) => {
      const link = (e.target as Element | null)?.closest?.("a[href]");
      const href = link?.getAttribute("href") || "";
      if (/^tel:/i.test(href)) trackContact("phone");
      else if (/^https?:\/\/(wa\.me|api\.whatsapp\.com|(www\.)?whatsapp\.com)\//i.test(href)) trackContact("whatsapp");
    },
    { capture: true }
  );
}
