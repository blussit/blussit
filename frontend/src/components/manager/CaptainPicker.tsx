import { useQueries, useQuery } from "@tanstack/react-query";
import { AlertCircle, Star } from "lucide-react";
import { staffDirectoryApi } from "../../api/admin";
import { bookingApi } from "../../api/booking";
import { Badge } from "../ui";
import { cn } from "../../lib/cn";
import type { Booking, User } from "../../types";

function queueDepthFor(captainId: string, centerBookings: Booking[], scheduledDate: string): number {
  return centerBookings.filter(
    (b) => b.captain_id === captainId && b.scheduled_date === scheduledDate && !["completed", "cancelled"].includes(b.status)
  ).length;
}

export function CaptainPicker({
  bookingId,
  captains,
  centerBookings,
  scheduledDate,
  selectedId,
  onSelect,
}: {
  bookingId: string;
  captains: User[];
  centerBookings: Booking[];
  scheduledDate: string;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  // Server-computed eligibility for THIS booking's exact time window — reuses
  // the real conflict-check logic so the manager sees why a captain can't
  // take it (busy until X, wallet balance) instead of only finding out via a
  // failed submit.
  const { data: eligibility } = useQuery({
    queryKey: ["eligible-captains", bookingId],
    queryFn: () => bookingApi.eligibleCaptains(bookingId),
  });
  const eligibilityById = new Map((eligibility || []).map((e) => [e.captain_id, e]));

  const performanceQueries = useQueries({
    queries: captains.map((c) => ({
      queryKey: ["captain-perf", c.id],
      queryFn: () => staffDirectoryApi.captainPerformance(c.id),
    })),
  });

  return (
    <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
      {captains.map((c, i) => {
        const perf = performanceQueries[i]?.data;
        const eligible = eligibilityById.get(c.id);
        const isIneligible = eligible?.eligible === false;
        const depth = queueDepthFor(c.id, centerBookings, scheduledDate);
        const selected = selectedId === c.id;

        return (
          <button
            key={c.id}
            type="button"
            disabled={isIneligible}
            onClick={() => onSelect(c.id)}
            className={cn(
              "flex w-full flex-col gap-1.5 rounded-xl border px-3.5 py-2.5 text-left transition-colors",
              selected ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]" : "border-gray-200 hover:bg-gray-50",
              isIneligible && "cursor-not-allowed opacity-60 hover:bg-transparent"
            )}
          >
            <div className="flex items-center justify-between">
              <span className="font-medium text-[var(--color-text-primary)]">{c.full_name}</span>
              {depth > 0 && <Badge tone={depth < 3 ? "warning" : "error"}>{depth} job{depth > 1 ? "s" : ""} today</Badge>}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
              {perf && (
                <span className="flex items-center gap-1">
                  <Star className="h-3 w-3 fill-amber-400 text-amber-400" /> {perf.average_rating || "—"} ({perf.total_reviews})
                </span>
              )}
              {perf?.avg_heading_punctuality_minutes != null && (
                <span>
                  {perf.avg_heading_punctuality_minutes <= 0
                    ? `~${Math.abs(Math.round(perf.avg_heading_punctuality_minutes))} min early on average`
                    : `~${Math.round(perf.avg_heading_punctuality_minutes)} min late on average`}
                </span>
              )}
            </div>
            {isIneligible && eligible?.reason && (
              <span className="flex items-center gap-1 text-xs font-medium text-[var(--color-error)]">
                <AlertCircle className="h-3 w-3" /> {eligible.reason}
              </span>
            )}
          </button>
        );
      })}
      {captains.length === 0 && <p className="text-sm text-[var(--color-text-secondary)]">No captains available at this center.</p>}
    </div>
  );
}
