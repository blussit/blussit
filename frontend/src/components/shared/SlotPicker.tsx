import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Timer } from "lucide-react";
import { serviceCenterApi, slotHoldApi } from "../../api/catalog";
import { Input } from "../ui";
import { maxBookingDateIST, todayIST } from "../../lib/date";
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
   * while this picker stays mounted); other customers see it as taken. */
  enableHold?: boolean;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["available-slots", serviceCenterId, date];
  const [holdError, setHoldError] = useState("");
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const heldRef = useRef<{ center: string; date: string; slot: string } | null>(null);

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
        if (prev === 45 && heldRef.current) acquire(heldRef.current.slot);
        return Math.max(prev - 1, 0);
      });
    }, 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableHold, secondsLeft != null]);

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
        label="Select Date"
        type="date"
        min={todayIST()}
        max={maxBookingDateIST()}
        hint="Bookings open up to 7 days in advance."
        value={date}
        onChange={(e) => {
          onDateChange(e.target.value);
          onChange("");
        }}
        required
      />

      {!serviceCenterId ? (
        <p className="text-xs text-[var(--color-text-secondary)]">Enter your address below to see available slots.</p>
      ) : !date ? null : (
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
            <Clock className="h-3.5 w-3.5" /> Select a time slot
          </p>
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
                      if (enableHold && heldRef.current?.slot !== s.key) {
                        releaseCurrent();
                        acquire(s.key);
                      }
                      onChange(s.key);
                    }}
                    className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                      selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                    }`}
                  >
                    <span className="font-mono-num">
                      {s.start}–{s.end}
                    </span>
                    <span className={`ml-1.5 text-xs ${selected ? "text-white/80" : s.status === "low" ? "text-[var(--color-error)]" : "text-[var(--color-text-secondary)]"}`}>
                      {s.status === "full" ? "· Fully booked" : s.status === "low" ? `· Only ${s.remaining} spot${s.remaining === 1 ? "" : "s"} left` : "· Available"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {enableHold && holdError && <p className="mt-2 text-xs text-[var(--color-error)]">{holdError}</p>}
          {enableHold && value && secondsLeft != null && secondsLeft > 0 && (
            <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-1 text-xs font-medium text-green-700">
              <Timer className="h-3.5 w-3.5" />
              Slot held for you · {Math.floor(secondsLeft / 60)}:{String(secondsLeft % 60).padStart(2, "0")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
