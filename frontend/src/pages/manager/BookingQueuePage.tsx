import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, Ban, CalendarClock, CheckCircle2, Clock, Phone, Sparkles } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { adminServiceCenterApi, staffDirectoryApi } from "../../api/admin";
import { Button, Card, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import { vehicleLabel } from "../../lib/constants";
import { CaptainPicker } from "../../components/manager/CaptainPicker";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { useAuth } from "../../context/AuthContext";
import { format, minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { ISSUE_LABELS, isOpenIssue, needsCaptain } from "../../lib/constants";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { useLiveChannel } from "../../lib/socket";
import type { Booking } from "../../types";

type View = "attention" | "late_starts" | "all";

// Needs a captain AND starts soon — no lead time left, this is the one that
// actually has to be assigned right now, not just "sometime today".
const isUrgentUnassigned = (b: Booking) => needsCaptain(b) && minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot) <= URGENT_ASSIGNMENT_MINUTES;

// Every booking the captain actually started late on, regardless of
// whether that flag has since been resolved — this is a monitoring list
// ("how often is this happening"), not just an actionable one, so it
// intentionally isn't limited to currently-open issues the way the
// "Flagged issues" section is.
const startedLate = (b: Booking) => b.captain_start_stage === "late" || b.captain_start_stage === "severely_late";

// "YYYY-MM" -> "August 2026" — built from plain y/m integers (never a
// timezone-sensitive Date.parse of the key itself) for the late-starts
// month filter.
const monthLabel = (key: string) => {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "long", year: "numeric" });
};

// One-click status filters for the "All bookings" view — same pill pattern
// as the captain dashboard's filters, so a manager can jump straight to
// e.g. every completed or cancelled booking instead of digging through a
// dropdown.
const STATUS_FILTERS = [
  { label: "All", value: "" },
  // Never in the assignment queue (they aren't confirmed bookings) — this
  // filter exists so a manager can FIND one when a customer rings up
  // saying they booked and you can't see it.
  { label: "Payment pending", value: "awaiting_payment" },
  { label: "Pending", value: "pending" },
  { label: "Assigned", value: "assigned" },
  { label: "On the way", value: "captain_on_the_way" },
  { label: "In progress", value: "service_started" },
  { label: "Completed", value: "completed" },
  { label: "Cancelled", value: "cancelled" },
  { label: "Rescheduled", value: "rescheduled" },
];

function byScheduledAsc(a: Booking, b: Booking) {
  const d = new Date(a.scheduled_date).getTime() - new Date(b.scheduled_date).getTime();
  return d !== 0 ? d : (a.scheduled_slot || "").localeCompare(b.scheduled_slot || "");
}

const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };
// Priority first (HIGH before MEDIUM before LOW), then within the same
// priority the existing cutoff/slot-timing order — matches the ops spec's
// tiebreak sequence exactly.
function byPriorityThenScheduled(a: Booking, b: Booking) {
  const p = (PRIORITY_RANK[a.priority] ?? 1) - (PRIORITY_RANK[b.priority] ?? 1);
  return p !== 0 ? p : byScheduledAsc(a, b);
}

const PRIORITY_OPTIONS: ("high" | "medium" | "low")[] = ["high", "medium", "low"];

function bookingLabel(b: Booking): string {
  if (b.combo_name) return b.combo_name;
  if (b.service_names?.length) return b.service_names.join(", ");
  return "Service";
}

/** The single clear answer to "what's going on with this booking" — blank
 * for a normal healthy booking, since there's nothing to explain. */
function WhatHappened({ booking }: { booking: Booking }) {
  if (booking.status === "cancelled") {
    return (
      <span className="flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
        <Ban className="h-3 w-3" /> Cancelled{booking.cancellation_reason ? ` — ${booking.cancellation_reason}` : ""}
      </span>
    );
  }
  if (booking.issue_flag) {
    const label = ISSUE_LABELS[booking.issue_flag] || booking.issue_flag;
    if (isOpenIssue(booking)) {
      return (
        <span className="flex items-center gap-1 text-xs font-medium text-amber-700">
          <AlertTriangle className="h-3 w-3" /> {label} — needs action
        </span>
      );
    }
    return <span className="text-xs text-[var(--color-text-secondary)]">{label} — resolved</span>;
  }
  return null;
}

export default function BookingQueuePage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const queryClient = useQueryClient();

  const [view, setView] = useState<View>("attention");
  const [statusFilter, setStatusFilter] = useState("");
  const [lateStartMonth, setLateStartMonth] = useState("");
  const [lateStartCaptainId, setLateStartCaptainId] = useState("");

  // "New since I last opened this tab" — not a running total. Persisted per
  // manager so it survives a refresh; missing (never-visited) reads as 0,
  // which correctly counts everything currently late-started as new on a
  // manager's very first-ever visit to this tab.
  const lateStartsSeenKey = user?.id ? `late_starts_seen_at:${user.id}` : null;
  const [lateStartsSeenAt, setLateStartsSeenAt] = useState<number>(() => {
    const stored = lateStartsSeenKey ? localStorage.getItem(lateStartsSeenKey) : null;
    return stored ? Number(stored) : 0;
  });
  const markLateStartsSeen = () => {
    const now = Date.now();
    setLateStartsSeenAt(now);
    if (lateStartsSeenKey) localStorage.setItem(lateStartsSeenKey, String(now));
  };

  const [assigningBooking, setAssigningBooking] = useState<Booking | null>(null);
  const [isReassign, setIsReassign] = useState(false);
  const [captainId, setCaptainId] = useState("");
  const [error, setError] = useState("");

  const [reschedulingBooking, setReschedulingBooking] = useState<Booking | null>(null);
  const [newDate, setNewDate] = useState("");
  const [newSlot, setNewSlot] = useState("");

  const [resolvingBooking, setResolvingBooking] = useState<Booking | null>(null);
  const [resolveNote, setResolveNote] = useState("");

  const [cancellingBooking, setCancellingBooking] = useState<Booking | null>(null);
  const [cancelReason, setCancelReason] = useState("");

  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null);
  const { data: center } = useQuery({ queryKey: ["center-detail-for-queue", centerId], queryFn: () => adminServiceCenterApi.get(centerId), enabled: !!centerId });

  // One query, fetched unfiltered — everything else (new/flagged/status
  // filter/sort) is derived from it client-side. Simpler than juggling two
  // separate server-filtered queries, and center booking volumes here don't
  // need more than a single page to stay complete.
  const centerBookingsQueryKey = ["center-bookings", centerId];
  const { data, isLoading } = useQuery({
    queryKey: centerBookingsQueryKey,
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!centerId,
    // Live-pushed over "center-bookings:{centerId}" (see below) — this
    // interval is now just the reconnect-window fallback, not the primary
    // update path.
    refetchInterval: 60000,
    refetchIntervalInBackground: true,
  });
  const items = data?.data || [];

  useLiveChannel(centerId ? `center-bookings:${centerId}` : null, () => {
    queryClient.invalidateQueries({ queryKey: centerBookingsQueryKey });
  });

  const { data: captains } = useQuery({
    queryKey: ["center-captains-list", centerId],
    queryFn: () => staffDirectoryApi.captainsForCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!centerId,
  });

  const captainName = (id?: string | null) => captains?.data.find((c) => c.id === id)?.full_name || "—";
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["center-bookings"] });

  // Excludes anything already flagged (isOpenIssue) — once the automated
  // sweep has flagged a booking (e.g. its window fully expired with no
  // captain), it belongs ONLY in "Flagged issues" below with that section's
  // more accurate, more severe wording. Without this exclusion the exact
  // same booking showed in BOTH sections at once with contradictory
  // messages — "Starting now — assign immediately" right next to "window
  // expired — needs action" for the same booking, which is exactly the
  // confusing double-listing this line exists to prevent.
  const newBookings = useMemo(() => items.filter((b) => needsCaptain(b) && !isOpenIssue(b)).sort(byPriorityThenScheduled), [items]);
  const openIssues = useMemo(() => items.filter(isOpenIssue).sort(byPriorityThenScheduled), [items]);
  const lateStartBookings = useMemo(
    () => items.filter(startedLate).sort((a, b) => new Date(b.scheduled_date).getTime() - new Date(a.scheduled_date).getTime()),
    [items]
  );
  // heading_at is set the moment a captain starts heading out — the exact
  // instant captain_start_stage gets decided — so it's what "new" means
  // here, independent of whichever month/captain filter is active below.
  const newLateStartsCount = useMemo(
    () => lateStartBookings.filter((b) => new Date(b.heading_at || 0).getTime() > lateStartsSeenAt).length,
    [lateStartBookings, lateStartsSeenAt]
  );
  const lateStartMonths = useMemo(() => {
    const keys = new Set(lateStartBookings.map((b) => b.scheduled_date.slice(0, 7))); // "YYYY-MM"
    return Array.from(keys).sort((a, b) => b.localeCompare(a));
  }, [lateStartBookings]);
  const lateStartCaptainIds = useMemo(() => {
    const ids = new Set(lateStartBookings.map((b) => b.captain_id).filter((id): id is string => Boolean(id)));
    return Array.from(ids);
  }, [lateStartBookings]);
  const visibleLateStarts = useMemo(
    () =>
      lateStartBookings.filter(
        (b) => (!lateStartMonth || b.scheduled_date.slice(0, 7) === lateStartMonth) && (!lateStartCaptainId || b.captain_id === lateStartCaptainId)
      ),
    [lateStartBookings, lateStartMonth, lateStartCaptainId]
  );
  const statusFiltered = useMemo(() => (statusFilter ? items.filter((b) => b.status === statusFilter) : items), [items, statusFilter]);
  const {
    filtered: filteredAll,
    search,
    setSearch,
    sortOrder,
    setSortOrder,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
  } = useBookingFilters(statusFiltered);

  // Supports "jump straight to this one booking" links from elsewhere in
  // the app (e.g. a booking row in a captain's profile) — ?highlight=<id>
  // switches to the "All bookings" view and searches for that booking's
  // own number, which narrows the table down to exactly that one row
  // without needing any scroll-to-row machinery in the shared DataTable.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const highlightId = searchParams.get("highlight");
    if (!highlightId || !items.length) return;
    const target = items.find((b) => b.id === highlightId);
    if (!target) return;
    setView("all");
    setStatusFilter("");
    setSearch(target.booking_number);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("highlight");
      return next;
    }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, searchParams]);

  const assignMutation = useMutation({
    mutationFn: () =>
      isReassign ? bookingApi.reassignCaptain(assigningBooking!.id, captainId) : bookingApi.assignCaptain(assigningBooking!.id, captainId),
    onSuccess: () => {
      invalidate();
      setAssigningBooking(null);
      setCaptainId("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const rescheduleMutation = useMutation({
    mutationFn: () => bookingApi.reschedule(reschedulingBooking!.id, newDate, newSlot),
    onSuccess: () => {
      invalidate();
      setReschedulingBooking(null);
      setNewDate("");
      setNewSlot("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const priorityMutation = useMutation({
    mutationFn: ({ id, priority }: { id: string; priority: "high" | "medium" | "low" }) => bookingApi.updatePriority(id, priority),
    onSuccess: invalidate,
  });

  const prioritySelector = (b: Booking) => (
    <div className="flex gap-1">
      {PRIORITY_OPTIONS.map((p) => (
        <button
          key={p}
          type="button"
          disabled={priorityMutation.isPending}
          onClick={(e) => {
            e.stopPropagation();
            if (p !== b.priority) priorityMutation.mutate({ id: b.id, priority: p });
          }}
          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors disabled:opacity-50 ${
            b.priority === p
              ? p === "high"
                ? "bg-[var(--color-error)] text-white"
                : p === "medium"
                  ? "bg-amber-500 text-white"
                  : "bg-gray-400 text-white"
              : "bg-gray-100 text-gray-500 hover:bg-gray-200"
          }`}
        >
          {p}
        </button>
      ))}
    </div>
  );

  const resolveMutation = useMutation({
    mutationFn: () => bookingApi.resolveIssue(resolvingBooking!.id, resolveNote || undefined),
    onSuccess: () => {
      invalidate();
      setResolvingBooking(null);
      setResolveNote("");
    },
  });

  const cancelMutation = useMutation({
    // A car on a visit is never cancelled alone from here — the manager
    // made one decision about one visit, so this cancels every vehicle on
    // it. (A customer can still drop a single car themselves from their
    // own booking page; this button is the manager's "cancel the booking"
    // action, and a visit only ever reads as one booking to them.) The two
    // endpoints return different shapes; the caller only cares it worked.
    mutationFn: async (): Promise<void> => {
      if (cancellingBooking!.booking_group_id) await bookingApi.cancelGroup(cancellingBooking!.booking_group_id, cancelReason);
      else await bookingApi.cancel(cancellingBooking!.id, cancelReason);
    },
    onSuccess: () => {
      invalidate();
      setCancellingBooking(null);
      setCancelReason("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  // A collapsed visit row shows one set of actions for cars that should
  // all be in the same state — but the whole reason for this fix is that,
  // pre-convergence, they might not be. Whichever car actually still
  // needs (re)assignment is the one the click acts on; the backend then
  // converges every other car on the visit onto that same captain
  // regardless of which one triggered it.
  const actionTarget = (slab: BookingSlab): Booking =>
    slab.bookings.find(needsCaptain) || slab.bookings.find(canReassign) || slab.primary;

  const openAssign = (booking: Booking, reassign: boolean) => {
    setAssigningBooking(booking);
    setIsReassign(reassign);
    setCaptainId(reassign ? booking.captain_id || "" : "");
    setError("");
  };

  // Mirrors the backend: a captain can be swapped while pending or assigned, but not
  // once they're already on the way — the heading/before-photo trail is locked in by then.
  const canReassign = (b: Booking) => b.status === "assigned";
  const canCancel = (b: Booking) => !["completed", "cancelled"].includes(b.status);

  // Sits inside a row that now opens the booking detail drawer on click
  // (DataTable's onRowClick) — stopPropagation here so clicking any of
  // these action buttons doesn't ALSO trigger that row-open behavior.
  const bookingActions = (b: Booking) => (
    <div className="flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
      {needsCaptain(b) && (
        <Button size="sm" onClick={() => openAssign(b, false)}>
          Assign captain
        </Button>
      )}
      {canReassign(b) && (
        <Button size="sm" variant="outline" onClick={() => openAssign(b, true)}>
          Reassign
        </Button>
      )}
      {isOpenIssue(b) && (
        <>
          <Button size="sm" variant="outline" onClick={() => setReschedulingBooking(b)}>
            Reschedule
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setResolvingBooking(b)}>
            <CheckCircle2 className="h-3.5 w-3.5" /> Resolve
          </Button>
        </>
      )}
      {canCancel(b) && (
        <Button size="sm" variant="ghost" onClick={() => setCancellingBooking(b)}>
          <Ban className="h-3.5 w-3.5" /> Cancel
        </Button>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Booking queue</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Assign captains and handle anything that needs your attention.</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setView("attention")}
          className={`rounded-full px-4 py-2 text-sm font-semibold transition-colors ${
            view === "attention" ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          Needs attention
          {newBookings.length + openIssues.length > 0 && (
            <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${view === "attention" ? "bg-white/20" : "bg-gray-300"}`}>
              {newBookings.length + openIssues.length}
            </span>
          )}
        </button>
        <button
          onClick={() => {
            setView("late_starts");
            markLateStartsSeen();
          }}
          className={`rounded-full px-4 py-2 text-sm font-semibold transition-colors ${
            view === "late_starts" ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          Late starts
          {newLateStartsCount > 0 && (
            <span className={`ml-2 rounded-full px-2 py-0.5 text-xs ${view === "late_starts" ? "bg-white/20" : "bg-gray-300"}`}>
              {newLateStartsCount}
            </span>
          )}
        </button>
        <button
          onClick={() => setView("all")}
          className={`rounded-full px-4 py-2 text-sm font-semibold transition-colors ${
            view === "all" ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
        >
          All bookings
        </button>
      </div>

      {view === "attention" ? (
        <div className="space-y-8">
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
              <CalendarClock className="h-4 w-4" /> New bookings — need a captain ({newBookings.length})
            </h2>
            {!isLoading && newBookings.length === 0 ? (
              <p className="rounded-xl bg-gray-50 p-4 text-sm text-[var(--color-text-secondary)]">Nothing waiting on assignment.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {toSlabs(newBookings).map((slab) => {
                  const b = slab.primary;
                  const minutesLeft = minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot);
                  const urgent = isUrgentUnassigned(b);
                  return (
                  <Card
                    key={slab.key}
                    className={`cursor-pointer transition-shadow hover:shadow-[var(--shadow-lifted)] ${urgent ? "border-l-4 border-l-[var(--color-error)] bg-red-50/40 p-4" : "p-4"}`}
                    onClick={() => setSelectedBooking(b)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="flex flex-wrap items-center gap-2 font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">
                          {slab.isVisit ? slab.bookings.map((x) => x.booking_number).join(" · ") : b.booking_number}
                          {/* One trip, several cars — dispatching them
                              separately would send two captains to one gate. */}
                          {slab.isVisit && (
                            <span className="rounded-full bg-black px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                              1 visit · {slab.vehicleCount} vehicles
                            </span>
                          )}
                        </p>
                        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                          {format(b.scheduled_date)} · {b.scheduled_slot}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1.5">
                        <span className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">₹{slab.totalAmount}</span>
                        {prioritySelector(b)}
                      </div>
                    </div>
                    {urgent && (
                      <div className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-error)]">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        {minutesLeft < -60
                          ? "Already past its scheduled time — assign urgently or reschedule"
                          : minutesLeft <= 0
                            ? "Starting now — assign immediately"
                            : `Starts in ${Math.round(minutesLeft)} min — assign now`}
                      </div>
                    )}
                    <div className="mt-2 space-y-1 text-sm text-[var(--color-text-secondary)]">
                      {(b.customer_name || b.customer_phone) && (
                        <p>
                          {b.customer_name}
                          {b.customer_phone && (
                            <span className="ml-2 inline-flex items-center gap-1">
                              <Phone className="h-3 w-3" /> {b.customer_phone}
                            </span>
                          )}
                        </p>
                      )}
                      <p className="flex items-center gap-1">
                        <Sparkles className="h-3 w-3" /> {bookingLabel(b)}
                      </p>
                    </div>
                    <div className="mt-3">{bookingActions(b)}</div>
                  </Card>
                  );
                })}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-amber-700">
              <AlertTriangle className="h-4 w-4" /> Flagged issues ({openIssues.length})
            </h2>
            {!isLoading && openIssues.length === 0 ? (
              <p className="rounded-xl bg-gray-50 p-4 text-sm text-[var(--color-text-secondary)]">No open issues right now.</p>
            ) : (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {openIssues.map((b) => (
                  <Card
                    key={b.id}
                    className="cursor-pointer border-l-4 border-l-amber-500 bg-amber-50/40 p-4 transition-shadow hover:shadow-[var(--shadow-lifted)]"
                    onClick={() => setSelectedBooking(b)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                          {format(b.scheduled_date)} · {b.scheduled_slot} · Captain: {captainName(b.captain_id)}
                        </p>
                      </div>
                      <div className="flex flex-col items-end gap-1.5">
                        <StatusBadge status={b.status} />
                        {prioritySelector(b)}
                      </div>
                    </div>
                    <div className="mt-2">
                      <WhatHappened booking={b} />
                    </div>
                    {b.issue_notes && <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">"{b.issue_notes}"</p>}
                    <div className="mt-3">{bookingActions(b)}</div>
                  </Card>
                ))}
              </div>
            )}
          </section>
        </div>
      ) : view === "late_starts" ? (
        <div className="space-y-3">
          {lateStartBookings.length > 0 && (
            <div className="flex flex-wrap gap-3">
              <div className="w-48">
                <Select label="Month" value={lateStartMonth} onChange={(e) => setLateStartMonth(e.target.value)}>
                  <option value="">All months</option>
                  {lateStartMonths.map((key) => (
                    <option key={key} value={key}>
                      {monthLabel(key)}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="w-48">
                <Select label="Captain" value={lateStartCaptainId} onChange={(e) => setLateStartCaptainId(e.target.value)}>
                  <option value="">All captains</option>
                  {lateStartCaptainIds.map((id) => (
                    <option key={id} value={id}>
                      {captainName(id)}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          )}
          {!isLoading && visibleLateStarts.length === 0 ? (
            <p className="rounded-xl bg-gray-50 p-4 text-sm text-[var(--color-text-secondary)]">
              {lateStartBookings.length === 0 ? "No late starts recorded." : "No late starts match these filters."}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              {visibleLateStarts.map((b) => (
                <Card
                  key={b.id}
                  className={`cursor-pointer transition-shadow hover:shadow-[var(--shadow-lifted)] ${
                    b.captain_start_stage === "severely_late"
                      ? "border-l-4 border-l-[var(--color-error)] bg-red-50/40 p-4"
                      : "border-l-4 border-l-amber-500 bg-amber-50/40 p-4"
                  }`}
                  onClick={() => setSelectedBooking(b)}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                      <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                        {format(b.scheduled_date)} · {b.scheduled_slot} · Captain: {captainName(b.captain_id)}
                      </p>
                    </div>
                    <StatusBadge status={b.status} />
                  </div>
                  <div className="mt-2 flex items-center gap-1.5 text-xs font-medium">
                    <Clock className={`h-3.5 w-3.5 ${b.captain_start_stage === "severely_late" ? "text-[var(--color-error)]" : "text-amber-700"}`} />
                    <span className={b.captain_start_stage === "severely_late" ? "text-[var(--color-error)]" : "text-amber-700"}>
                      {b.captain_start_stage === "severely_late" ? "Started significantly late" : "Started late"}
                      {b.late_penalty_pct ? ` — ${b.late_penalty_pct}% pay penalty applied` : ""}
                    </span>
                  </div>
                  {isOpenIssue(b) && <p className="mt-1 text-xs text-amber-700">Still flagged — needs action</p>}
                  <div className="mt-3">{bookingActions(b)}</div>
                </Card>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.label}
                onClick={() => setStatusFilter(f.value)}
                className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                  statusFilter === f.value ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <BookingFilterBar
            search={search}
            onSearchChange={setSearch}
            sortOrder={sortOrder}
            onSortOrderChange={setSortOrder}
            dateFrom={dateFrom}
            onDateFromChange={setDateFrom}
            dateTo={dateTo}
            onDateToChange={setDateTo}
          />

          {/* One row per VISIT — several cars washed on one trip are one
              booking to assign, reschedule or cancel. Every action below
              still fires on a single underlying car id (slab.primary),
              because the backend itself converges the whole visit onto
              whatever's chosen there (assigning/reassigning a captain,
              rescheduling, resolving a flag, changing priority) — this
              table just has to stop OFFERING two separate decisions for
              one visit, which is what let a manager assign two different
              cars of the same trip to two different captains. */}
          <DataTable<BookingSlab & { id: string }>
            isLoading={isLoading}
            data={toSlabs(filteredAll).map((slab) => ({ ...slab, id: slab.key }))}
            emptyTitle="No bookings"
            onRowClick={(slab) => setSelectedBooking(slab.primary)}
            columns={[
              {
                header: "Booking #",
                accessor: (slab) => (
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono-num">{slab.isVisit ? slab.bookings.map((b) => b.booking_number).join(" · ") : slab.primary.booking_number}</span>
                    {slab.isVisit && (
                      <span
                        title="Several vehicles washed on one visit — one trip, one captain, one payment"
                        className="rounded-full bg-gray-900 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white"
                      >
                        {slab.vehicleCount} vehicles
                      </span>
                    )}
                  </span>
                ),
              },
              { header: "Customer", accessor: (slab) => slab.primary.customer_name || "—" },
              {
                header: "Vehicle & service",
                accessor: (slab) =>
                  slab.isVisit ? (
                    <ul className="space-y-0.5 text-xs">
                      {slab.bookings.map((b, i) => (
                        <li key={b.id}>
                          <span className="font-mono-num mr-1 text-gray-400">{i + 1}.</span>
                          {vehicleLabel(b) || "—"} <span className="text-[var(--color-text-secondary)]">— {b.combo_name || b.service_names?.join(", ") || "—"}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="text-xs text-[var(--color-text-secondary)]">{bookingLabel(slab.primary)}</span>
                  ),
              },
              { header: "Date", accessor: (slab) => `${format(slab.primary.scheduled_date)} · ${slab.primary.scheduled_slot}` },
              { header: "Captain", accessor: (slab) => captainName(slab.primary.captain_id) },
              { header: "Amount", accessor: (slab) => <span className="font-mono-num">₹{slab.totalAmount}</span> },
              { header: "Status", accessor: (slab) => <StatusBadge status={slab.status} /> },
              { header: "What happened", accessor: (slab) => <WhatHappened booking={slab.primary} /> },
              { header: "", accessor: (slab) => bookingActions(actionTarget(slab)) },
            ]}
          />
        </div>
      )}

      <Modal open={!!assigningBooking} onClose={() => setAssigningBooking(null)} title={isReassign ? "Reassign captain" : "Assign captain"}>
        <div className="space-y-4">
          {assigningBooking && (
            <CaptainPicker
              bookingId={assigningBooking.id}
              captains={captains?.data || []}
              centerBookings={items}
              scheduledDate={assigningBooking.scheduled_date}
              selectedId={captainId}
              onSelect={setCaptainId}
            />
          )}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button
            className="w-full"
            disabled={!captainId || (isReassign && captainId === assigningBooking?.captain_id)}
            isLoading={assignMutation.isPending}
            onClick={() => assignMutation.mutate()}
          >
            {isReassign ? "Confirm reassignment" : "Confirm assignment"}
          </Button>
        </div>
      </Modal>

      <Modal open={!!reschedulingBooking} onClose={() => setReschedulingBooking(null)} title="Reschedule booking">
        <p className="mb-3 text-sm text-[var(--color-text-secondary)]">
          This clears the current captain and issue flag, and moves the booking to a new time — you'll need to assign a captain
          again afterward.
        </p>
        <div className="space-y-4">
          <SlotPicker serviceCenterId={reschedulingBooking?.service_center_id} date={newDate} onDateChange={setNewDate} value={newSlot} onChange={setNewSlot} />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button className="w-full" disabled={!newDate || !newSlot} isLoading={rescheduleMutation.isPending} onClick={() => rescheduleMutation.mutate()}>
            Confirm reschedule
          </Button>
        </div>
      </Modal>

      <Modal open={!!resolvingBooking} onClose={() => setResolvingBooking(null)} title="Resolve issue">
        <p className="mb-3 text-sm text-[var(--color-text-secondary)]">
          Clears the flag without reassigning or rescheduling — e.g. you called the captain and confirmed things are on track.
        </p>
        <Input label="Note (optional)" value={resolveNote} onChange={(e) => setResolveNote(e.target.value)} placeholder="Called captain, on the way now" />
        <Button className="mt-4 w-full" isLoading={resolveMutation.isPending} onClick={() => resolveMutation.mutate()}>
          Mark resolved
        </Button>
      </Modal>

      <Modal open={!!cancellingBooking} onClose={() => setCancellingBooking(null)} title={cancellingBooking?.booking_group_id ? "Cancel visit" : "Cancel booking"}>
        <p className="mb-3 text-sm text-[var(--color-text-secondary)]">
          {cancellingBooking?.booking_group_id
            ? "This cancels every vehicle on this visit outright — the customer and captain (if assigned) are notified."
            : "This cancels the booking outright — the customer and captain (if assigned) are notified."}
        </p>
        <textarea
          className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
          rows={3}
          placeholder="Reason (min 3 characters)"
          value={cancelReason}
          onChange={(e) => setCancelReason(e.target.value)}
        />
        {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
        <div className="mt-4 flex gap-2">
          <Button variant="outline" className="flex-1" onClick={() => setCancellingBooking(null)}>
            Back
          </Button>
          <Button
            variant="danger"
            className="flex-1"
            isLoading={cancelMutation.isPending}
            disabled={cancelReason.trim().length < 3}
            onClick={() => cancelMutation.mutate()}
          >
            Cancel booking
          </Button>
        </div>
      </Modal>

      <BookingDetailDrawer
        booking={selectedBooking}
        onClose={() => setSelectedBooking(null)}
        captainName={selectedBooking ? captainName(selectedBooking.captain_id) : null}
        centerName={center?.name}
      />
    </div>
  );
}
