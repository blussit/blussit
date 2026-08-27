import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock } from "lucide-react";
import { serviceCenterApi } from "../../api/catalog";
import { Input } from "../ui";
import { todayIST } from "../../lib/date";
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
}: {
  serviceCenterId: string | undefined;
  date: string;
  onDateChange: (date: string) => void;
  value: string;
  onChange: (slotKey: string) => void;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["available-slots", serviceCenterId, date];

  const { data: slots, isLoading } = useQuery({
    queryKey,
    queryFn: () => serviceCenterApi.availableSlots(serviceCenterId!, date),
    enabled: !!serviceCenterId && !!date,
    refetchInterval: 60000,
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
                    onClick={() => onChange(s.key)}
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
        </div>
      )}
    </div>
  );
}
