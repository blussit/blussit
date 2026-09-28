import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Timer } from "lucide-react";
import { serviceCenterApi, slotHoldApi } from "../../api/catalog";
import { Input } from "../ui";
import { maxBookingDateIST, todayIST, formatTime12 } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { useLiveChannel } from "../../lib/socket";

/**
 * Admin-managed booking slots — replaces free-pick exact-time selection.
 * Never shown raw capacity: the backend only ever sends "available" /
 * "low" (+ the real remaining count, per the exact wording the customer
 * spec calls for) / "full". Live-updated over the "slots:{center}:{date}"
 * WebSocket channel — any booking/cancellation/admin capacity change
 * anywhere pushes an immediate refetch here instead of waiting out a poll
 * interval. The polling interval below is kept as a sparse fallback for
 * when the socket is reconnecting, not the primary update mechanism
 * anymore. The backend transaction at submit time is still the
 * authoritative check regardless of what's displayed here.
 */
const HOLD_RENEW_CAP_MS = 15 * 60_000;

export function SlotPicker({
  serviceCenterId,
  date,
  onDateChange,
  value,
  onChange,
  enableHold = false,
}: {
  serviceCenterId: string | undefined;
  date: string;
  onDateChange: (date: string) => void;
  value: string;
  onChange: (slotKey: string) => void;
  /** Theater-seat mode: picking a slot claims it for 5 minutes (renewed
   * while this picker is on screen, up to HOLD_RENEW_CAP_MS); other
   * customers see it as taken. */
  enableHold?: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["available-slots", serviceCenterId, date];
  const [holdError, setHoldError] = useState("");
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const heldRef = useRef<{ center: string; date: string; slot: string } | null>(null);
  // When the customer last tapped the slot they hold — renewals stop
  // HOLD_RENEW_CAP_MS after it, so a forgotten tab can't keep a seat away
  // from everyone else all day.
  const heldSinceRef = useRef(0);
  const holdExpiresAtRef = useRef(0);

  const releaseCurrent = () => {
    const h = heldRef.current;
    if (h) {
      heldRef.current = null;
      setSecondsLeft(null);
      slotHoldApi.release(h.center, h.date, h.slot);
    }
  };

  const acquire = async (slotKey: string) => {
    if (!serviceCenterId || !date) return;
    try {
      const res = await slotHoldApi.hold(serviceCenterId, date, slotKey);
      heldRef.current = { center: serviceCenterId, date, slot: slotKey };
      holdExpiresAtRef.current = Date.now() + res.hold_seconds * 1000;
      setSecondsLeft(res.hold_seconds);
      setHoldError("");
    } catch {
      // Slot filled between render and tap — refresh and let them re-pick.
      setHoldError("That slot was just taken — please pick another.");
      onChange("");
      queryClient.invalidateQueries({ queryKey });
    }
  };

  // Countdown + auto-renew while the customer is still here.
  useEffect(() => {
    if (!enableHold || secondsLeft == null) return;
    const t = setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev == null) return prev;
        if (
          prev === 45 &&
          heldRef.current &&
          document.visibilityState === "visible" &&
          Date.now() - heldSinceRef.current < HOLD_RENEW_CAP_MS
        ) {
          acquire(heldRef.current.slot);
        }
        return Math.max(prev - 1, 0);
      });
    }, 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableHold, secondsLeft != null]);

  // Hidden tabs skip the renewal above (and throttle the countdown), so
  // top the hold up when the customer comes back if it's about to lapse.
  useEffect(() => {
    if (!enableHold) return;
    const onVisible = () => {
      const h = heldRef.current;
      if (document.visibilityState !== "visible" || !h) return;
      if (Date.now() - heldSinceRef.current >= HOLD_RENEW_CAP_MS) return;
      if (holdExpiresAtRef.current - Date.now() > 45_000) return;
      acquire(h.slot);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableHold, serviceCenterId, date]);

  // Leaving the picker (or switching date) frees the seat immediately.
  useEffect(() => {
    if (!enableHold) return;
    return () => releaseCurrent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableHold, serviceCenterId, date]);

  const { data: slots, isLoading, isError, error } = useQuery({
    queryKey,
    queryFn: () => serviceCenterApi.availableSlots(serviceCenterId!, date),
    enabled: !!serviceCenterId && !!date,
    refetchInterval: 60000,
    // A 400 here is a policy answer (date outside the booking window),
    // not a transient failure — retrying it three times just delays the
    // error message below.
    retry: false,
  });

  useLiveChannel(serviceCenterId && date ? `slots:${serviceCenterId}:${date}` : null, () => {
    queryClient.invalidateQueries({ queryKey });
  });

  return (
    <div className="space-y-4">
      <Input
        label="Date"
        type="date"
        min={todayIST()}
        max={maxBookingDateIST()}
        value={date}
        onChange={(e) => {
          onDateChange(e.target.value);
          onChange("");
        }}
        required
      />

      {!serviceCenterId ? (
        <p className="text-xs text-[var(--color-text-secondary)]">Add your address to see the slots.</p>
      ) : !date ? null : (
        <div>
          <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Time slot</p>
          {isLoading ? (
            <p className="text-sm text-[var(--color-text-secondary)]">Loading slots…</p>
          ) : isError ? (
            <p className="text-sm text-[var(--color-error)]">{getErrorMessage(error)}</p>
          ) : !slots?.length ? (
            <p className="text-sm text-[var(--color-error)]">No slots are configured for this service center yet.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {slots.map((s) => {
                const disabled = s.status === "full";
                const selected = value === s.key;
                return (
                  <button
                    key={s.key}
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      // Re-tapping a slot whose hold lapsed claims it again.
                      if (enableHold && (heldRef.current?.slot !== s.key || !secondsLeft)) {
                        if (heldRef.current?.slot !== s.key) releaseCurrent();
                        heldSinceRef.current = Date.now();
                        acquire(s.key);
                      }
                      onChange(s.key);
                    }}
                    className={`rounded-full border-2 px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                      selected ? "border-black bg-[#FFF4CD] text-black" : "border-[#F3E5B5] bg-white text-gray-700 hover:border-gray-400"
                    }`}
                  >
                    <span className="font-mono-num">
                      {formatTime12(s.start)} – {formatTime12(s.end)}
                    </span>
                    {s.status === "full" && <span className="ml-1.5 text-xs text-gray-500">· Full</span>}
                    {s.status === "low" && (
                      <span className="ml-1.5 text-xs font-semibold text-black">
                        · {s.remaining} spot{s.remaining === 1 ? "" : "s"} left
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
          {enableHold && holdError && <p className="mt-2 text-xs text-[var(--color-error)]">{holdError}</p>}
          {enableHold && value && secondsLeft != null && secondsLeft > 0 && (
            <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-gray-500">
              <Timer className="h-3.5 w-3.5" />
              Held for you · {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
