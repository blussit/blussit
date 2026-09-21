/**
 * Warm-up for the booking flow. Everything here is best-effort and silent:
 * it only makes the NEXT screen instant, it never changes what is shown.
 *
 * The public pages other than the landing page are code-split (they are not
 * needed to paint the home page). These loaders are shared between the lazy
 * routes in App.tsx and the idle prefetch below, so "Book Now" finds its
 * code and its catalogue data already in the browser.
 */
import type { QueryClient } from "@tanstack/react-query";
import { bookingPolicyApi, catalogApi, vehicleTypeApi } from "../api/catalog";

export const loadBookPage = () => import("../pages/public/BookPage");
export const loadLoginPage = () => import("../pages/auth/LoginPage");

let warmed = false;

/** Idle-time prefetch of the booking wizard: its code + the catalogue it opens with. */
export function prefetchBooking(queryClient: QueryClient): void {
  if (warmed || typeof window === "undefined") return;
  const saveData = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData;
  if (saveData) return;
  warmed = true;
  const run = () => {
    void loadBookPage();
    void queryClient.prefetchQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
    void queryClient.prefetchQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
    void queryClient.prefetchQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get, staleTime: 5 * 60 * 1000 });
  };
  const idle = (window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }).requestIdleCallback;
  const start = () => (idle ? idle(run, { timeout: 4000 }) : window.setTimeout(run, 1500));
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true });
}
