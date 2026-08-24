// Provider-agnostic event tracking. Wire this up to GA4, Meta Pixel,
// PostHog, etc. later without touching component code.

export type AnalyticsEvent =
  | "page_view"
  | "hero_cta_click"
  | "services_viewed"
  | "early_access_click"
  | "form_started"
  | "form_completed"
  | "launch_cta_click";

export function track(event: AnalyticsEvent, meta?: Record<string, unknown>) {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.log(`[analytics] ${event}`, meta ?? {});
  }
  // Example future wiring:
  // window.gtag?.('event', event, meta);
}
