import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { slotHoldApi } from "../../api/catalog";

/** Renewals stop this long after the customer picked the slot, so a
 *  forgotten tab can't keep a seat away from everyone else all day. */
const HOLD_RENEW_CAP_MS = 15 * 60_000;
/** Renew when this little of the hold is left. */
const RENEW_AT_S = 45;

/**
 * The theater-seat hold for the booking flow: picking a slot claims it for
 * a few minutes (POST /bookings/hold, renewed while the customer is still
 * here) so nobody else can take it mid-checkout. It belongs to the FLOW,
 * not to the slot grid — the customer picks the time on one screen and
 * books on the next, and the seat must stay theirs across that move.
 *
 * Any change of center / day / slot frees the old seat first; leaving the
 * page frees it too. A hold the server refuses (the slot filled up, or the
 * address moved the visit to a center where it isn't open) calls onLost.
 * The booking itself sends the same holder key, which turns the hold into
 * the booking server-side.
 */
export function useSlotHold({
  centerId,
  date,
  slot,
  enabled,
  onLost,
}: {
  centerId: string;
  date: string;
  slot: string;
  enabled: boolean;
  onLost: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const held = useRef<{ center: string; date: string; slot: string } | null>(null);
  const target = useRef("");
  const pickedAt = useRef(0);
  const expiresAt = useRef(0);
  const renewing = useRef(false);
  // After a blip (rate limit / server / network) don't retry every tick.
  const retryAfter = useRef(0);
  const onLostRef = useRef(onLost);
  onLostRef.current = onLost;

  const release = useCallback(() => {
    const h = held.current;
    held.current = null;
    setSecondsLeft(null);
    if (h) void slotHoldApi.release(h.center, h.date, h.slot);
  }, []);

  const acquire = useCallback(
    async (center: string, day: string, key: string) => {
      const want = `${center}|${day}|${key}`;
      renewing.current = true;
      try {
        const res = await slotHoldApi.hold(center, day, key);
        if (target.current !== want) {
          // Moved on while this was in flight — give that seat straight back.
          void slotHoldApi.release(center, day, key);
          return;
        }
        held.current = { center, date: day, slot: key };
        expiresAt.current = Date.now() + res.hold_seconds * 1000;
        setSecondsLeft(res.hold_seconds);
      } catch (err) {
        if (target.current !== want) return;
        const status = (err as { response?: { status?: number } } | null)?.response?.status;
        if (!status || status === 429 || status >= 500) {
          // Not "taken" — a rate limit or a network/server blip. Keep the
          // customer's pick: a renewal tries again shortly; a first claim
          // simply goes without a hold (the booking still checks seats).
          retryAfter.current = Date.now() + 15_000;
          return;
        }
        target.current = "";
        held.current = null;
        setSecondsLeft(null);
        queryClient.invalidateQueries({ queryKey: ["available-slots", center, day] });
        onLostRef.current("That time was just taken — please pick another.");
      } finally {
        renewing.current = false;
      }
    },
    [queryClient]
  );

  useEffect(() => {
    if (!enabled || !centerId || !date || !slot) {
      target.current = "";
      release();
      return;
    }
    const want = `${centerId}|${date}|${slot}`;
    if (target.current === want) return;
    release();
    target.current = want;
    pickedAt.current = Date.now();
    void acquire(centerId, date, slot);
  }, [enabled, centerId, date, slot, acquire, release]);

  // Leaving the flow frees the seat at once (and forgets it, so a remount —
  // React's dev double-mount included — claims it again).
  useEffect(
    () => () => {
      target.current = "";
      release();
    },
    [release]
  );

  // Closing the tab / navigating away never unmounts React — free the seat
  // with a request that outlives the page. (Restored from the back/forward
  // cache, the next renewal simply claims it again.)
  useEffect(() => {
    const onHide = () => {
      const h = held.current;
      if (h) slotHoldApi.releaseOnExit(h.center, h.date, h.slot);
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);

  const renewIfDue = useCallback(() => {
    const h = held.current;
    if (!h || renewing.current || document.visibilityState !== "visible") return;
    if (Date.now() < retryAfter.current) return;
    if (Date.now() - pickedAt.current >= HOLD_RENEW_CAP_MS) return;
    if (expiresAt.current - Date.now() > RENEW_AT_S * 1000) return;
    void acquire(h.center, h.date, h.slot);
  }, [acquire]);

  const ticking = secondsLeft != null;
  useEffect(() => {
    if (!ticking) return;
    const timer = window.setInterval(() => {
      setSecondsLeft(Math.max(0, Math.round((expiresAt.current - Date.now()) / 1000)));
      renewIfDue();
    }, 1000);
    // Hidden tabs throttle timers — top the hold up on coming back.
    document.addEventListener("visibilitychange", renewIfDue);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", renewIfDue);
    };
  }, [ticking, renewIfDue]);

  /** Tapping the same slot again after its hold lapsed claims it afresh. */
  const reclaim = useCallback(() => {
    const h = held.current;
    if (!h || (secondsLeft ?? 0) > 0) return;
    pickedAt.current = Date.now();
    void acquire(h.center, h.date, h.slot);
  }, [acquire, secondsLeft]);

  return { secondsLeft, reclaim };
}
