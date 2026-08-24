import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { AlertTriangle, Ban, CalendarClock, CheckCircle2, Clock, Eye, Phone, Sparkles } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { adminServiceCenterApi, staffDirectoryApi } from "../../api/admin";
import { Button, Card, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { CaptainPicker } from "../../components/manager/CaptainPicker";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { useAuth } from "../../context/AuthContext";
import { format, minutesUntilSlotStart, todayIST, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { ISSUE_LABELS, isOpenIssue, needsCaptain } from "../../lib/constants";
import { useBookingFilters } from "../../lib/useBookingFilters";
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
  const navigate = useNavigate();
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
  const [newTime, setNewTime] = useState("");

  const [resolvingBooking, setResolvingBooking] = useState<Booking | null>(null);
  const [resolveNote, setResolveNote] = useState("");

  const [cancellingBooking, setCancellingBooking] = useState<Booking | null>(null);
  const [cancelReason, setCancelReason] = useState("");

  // One query, fetched unfiltered — everything else (new/flagged/status
  // filter/sort) is derived from it client-side. Simpler than juggling two
  // separate server-filtered queries, and center booking volumes here don't
  // need more than a single page to stay complete.
  const { data, isLoading } = useQuery({
    queryKey: ["center-bookings", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!centerId,
    refetchInterval: 10000,
    refetchIntervalInBackground: true,
  });
  const items = data?.data || [];

  const { data: captains } = useQuery({
    queryKey: ["center-captains-list", centerId],
    queryFn: () => staffDirectoryApi.captainsForCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!centerId,
  });

  // Bounds the reschedule "New time" picker to this store's actual working
  // hours (admin-configured) instead of a generic 24-hour dial — rarely
  // changes, so this is cheap to keep around for the lifetime of the page.
  const { data: center } = useQuery({
    queryKey: ["center-detail", centerId],
    queryFn: () => adminServiceCenterApi.get(centerId),
    enabled: !!centerId,
    staleTime: 5 * 60 * 1000,
  });

  const captainName = (id?: string | null) => captains?.data.find((c) => c.id === id)?.full_name || "—";
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["center-bookings"] });

  const newBookings = useMemo(() => items.filter(needsCaptain).sort(byScheduledAsc), [items]);
  const openIssues = useMemo(() => items.filter(isOpenIssue).sort(byScheduledAsc), [items]);
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
    mutationFn: () => bookingApi.reschedule(reschedulingBooking!.id, newDate, newTime),
    onSuccess: () => {
      invalidate();
      setReschedulingBooking(null);
      setNewDate("");
      setNewTime("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const resolveMutation = useMutation({
    mutationFn: () => bookingApi.resolveIssue(resolvingBooking!.id, resolveNote || undefined),
    onSuccess: () => {
      invalidate();
      setResolvingBooking(null);
      setResolveNote("");
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => bookingApi.cancel(cancellingBooking!.id, cancelReason),
    onSuccess: () => {
      invalidate();
      setCancellingBooking(null);
      setCancelReason("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

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

  const bookingActions = (b: Booking) => (
    <div className="flex flex-wrap items-center gap-2">
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
      <Button size="sm" variant="ghost" onClick={() => navigate(`/manager/bookings/${b.id}`)}>
        <Eye className="h-3.5 w-3.5" />
      </Button>
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
                {newBookings.map((b) => {
                  const minutesLeft = minutesUntilSlotStart(b.scheduled_date, b.scheduled_slot);
                  const urgent = isUrgentUnassigned(b);
                  return (
                  <Card key={b.id} className={urgent ? "border-l-4 border-l-[var(--color-error)] bg-red-50/40 p-4" : "p-4"}>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                          {format(b.scheduled_date)} · {b.scheduled_slot}
                        </p>
                      </div>
                      <span className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">₹{b.total_amount}</span>
                    </div>
                    {urgent && (
                      <div className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-error)]">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        {minutesLeft <= 0 ? "Starting now — assign immediately" : `Starts in ${Math.round(minutesLeft)} min — assign now`}
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
                  <Card key={b.id} className="border-l-4 border-l-amber-500 bg-amber-50/40 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                          {format(b.scheduled_date)} · {b.scheduled_slot} · Captain: {captainName(b.captain_id)}
                        </p>
                      </div>
                      <StatusBadge status={b.status} />
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
                  className={
                    b.captain_start_stage === "severely_late"
                      ? "border-l-4 border-l-[var(--color-error)] bg-red-50/40 p-4"
                      : "border-l-4 border-l-amber-500 bg-amber-50/40 p-4"
                  }
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

          <DataTable<Booking>
            isLoading={isLoading}
            data={filteredAll}
            emptyTitle="No bookings"
            columns={[
              { header: "Booking #", accessor: (b) => <span className="font-mono-num">{b.booking_number}</span> },
              { header: "Customer", accessor: (b) => b.customer_name || "—" },
              { header: "Date", accessor: (b) => `${format(b.scheduled_date)} · ${b.scheduled_slot}` },
              { header: "Captain", accessor: (b) => captainName(b.captain_id) },
              { header: "Amount", accessor: (b) => <span className="font-mono-num">₹{b.total_amount}</span> },
              { header: "Status", accessor: (b) => <StatusBadge status={b.status} /> },
              { header: "What happened", accessor: (b) => <WhatHappened booking={b} /> },
              { header: "", accessor: (b) => bookingActions(b) },
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
          <Input label="New date" type="date" min={todayIST()} value={newDate} onChange={(e) => setNewDate(e.target.value)} />
          <Input
            label="New time"
            type="time"
            min={center?.working_hours_start}
            max={center?.working_hours_end}
            value={newTime}
            onChange={(e) => setNewTime(e.target.value)}
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button className="w-full" disabled={!newDate || !newTime} isLoading={rescheduleMutation.isPending} onClick={() => rescheduleMutation.mutate()}>
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

      <Modal open={!!cancellingBooking} onClose={() => setCancellingBooking(null)} title="Cancel booking">
        <p className="mb-3 text-sm text-[var(--color-text-secondary)]">This cancels the booking outright — the customer and captain (if assigned) are notified.</p>
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
    </div>
  );
}
