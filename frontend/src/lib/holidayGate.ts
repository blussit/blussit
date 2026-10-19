import { useEffect, useState } from "react";

/**
 * Who gets the visitor's one popup this visit: a holiday notice wins over
 * the landing offer. The global HolidayGate reports what it found
 * ("showing" / "none"); LaunchOfferPopup waits for that answer (at most
 * GATE_WAIT_MS) and stays away when a holiday popup took the visit.
 */
export type HolidayGateState = "pending" | "none" | "showing";

let state: HolidayGateState = "pending";
const listeners = new Set<() => void>();

export function setHolidayGate(next: HolidayGateState): void {
  if (state === next) return;
  // Once a holiday popup has shown, this visit's answer stays "showing".
  if (state === "showing" && next !== "showing") return;
  state = next;
  listeners.forEach((fn) => fn());
}

/** How long the offer popup waits for the holiday check before going ahead. */
const GATE_WAIT_MS = 2500;

export function useHolidayGate(): HolidayGateState {
  const [value, setValue] = useState<HolidayGateState>(state);
  useEffect(() => {
    const update = () => setValue(state);
    listeners.add(update);
    update();
    const timer = window.setTimeout(() => setValue((v) => (v === "pending" ? "none" : v)), GATE_WAIT_MS);
    return () => {
      listeners.delete(update);
      window.clearTimeout(timer);
    };
  }, []);
  return value;
}

const SEEN_PREFIX = "blussit:holidaySeen:";
/** Same key LaunchOfferPopup uses — a holiday popup skips the offer for the session. */
export const OFFER_POPUP_SEEN_KEY = "blussit:offerPopupSeen";

export function holidaySeen(id: string): boolean {
  try {
    return !!sessionStorage.getItem(SEEN_PREFIX + id);
  } catch {
    return false;
  }
}

export function markHolidaySeen(id: string): void {
  try {
    sessionStorage.setItem(SEEN_PREFIX + id, "1");
    sessionStorage.setItem(OFFER_POPUP_SEEN_KEY, "1");
  } catch {
    // storage blocked: it may show again on the next page view
  }
}
