import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, ChevronLeft, ChevronRight, ClipboardEdit, LogOut, Pencil, RotateCcw, ShieldCheck, Star, Trash2 } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { analyticsApi } from "../../api/admin";
import { reviewApi } from "../../api/engagement";
import { Badge, Button, Card, DataTable, ErrorState, Select, StatusBadge } from "../../components/ui";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { EditBookingModal } from "../../components/shared/EditBookingModal";
import { StaffCancelDialog } from "../../components/shared/StaffCancelDialog";
import { CustomerEditedChip } from "../../components/shared/BookingStaffExtras";
import type { StaffBooking } from "../../api/staffBookings";
import { normalisePhoneSearch, Pager, useDebouncedValue } from "../../components/shared/ListControls";
import BookingQueuePage from "../manager/BookingQueuePage";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatSlot } from "../../lib/date";
import { bookingServiceLabel, toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import { vehicleLabel } from "../../lib/constants";
import { bookingCarAndService, toTitle } from "../../lib/titleCase";
import type { Booking } from "../../types";

/**
 * Section 10/11 of the BLUSSIT UX update — admin never sees every booking
 * from every center at once. First screen is a per-center summary; only
 * after picking one center does its booking list load, and only after
 * picking a booking does its full detail (via the shared
 * BookingDetailDrawer) open.
 */
export default function AdminBookingsPage() {
  const [selectedCenterId, setSelectedCenterId] = useState<string | null>(null);
  const [showRecycleBin, setShowRecycleBin] = useState(false);

  // A booking notification lands here as ?highlight=<id> (via
  // /admin/bookings/:id) — open that booking's center and its detail
  // straight away instead of the bare center picker.
  const [params, setParams] = useSearchParams();
  const highlightId = params.get("highlight");
  const highlightQuery = useQuery({
    queryKey: ["admin-highlight-booking", highlightId],
    queryFn: () => bookingApi.get(highlightId!),
    enabled: !!highlightId,
    retry: false,
  });
  const highlighted = highlightQuery.data;
  useEffect(() => {
    if (highlighted?.service_center_id) setSelectedCenterId(highlighted.service_center_id);
  }, [highlighted]);
  const clearHighlight = () => {
    const next = new URLSearchParams(params);
    next.delete("highlight");
    setParams(next, { replace: true });
  };

  const view = showRecycleBin ? (
    <RecycleBinView backLabel={selectedCenterId ? "Back To Bookings" : "All Service Centers"} onBack={() => setShowRecycleBin(false)} />
  ) : selectedCenterId ? (
    <CenterBookings centerId={selectedCenterId} onBack={() => setSelectedCenterId(null)} onShowRecycleBin={() => setShowRecycleBin(true)} />
  ) : (
    <ServiceCenterOverview onSelect={setSelectedCenterId} onShowRecycleBin={() => setShowRecycleBin(true)} />
  );
  return (
    <>
      {/* The booking a notification pointed at didn't load — say so rather
          than silently landing on the center picker. */}
      {highlightId && highlightQuery.isError && !highlighted && (
        <p role="alert" className="mb-4 text-sm text-[var(--color-text-secondary)]">
          Couldn't open that booking.{" "}
          <button
            type="button"
            className="font-semibold text-[var(--color-primary)] hover:underline disabled:opacity-60"
            disabled={highlightQuery.isFetching}
            onClick={() => void highlightQuery.refetch()}
          >
            Try Again
          </button>
        </p>
      )}
      {view}
      <BookingDetailDrawer booking={highlightId && highlighted ? highlighted : null} onClose={clearHighlight} />
    </>
  );
}

function ServiceCenterOverview({ onSelect, onShowRecycleBin }: { onSelect: (centerId: string) => void; onShowRecycleBin: () => void }) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["admin-center-summaries"], queryFn: analyticsApi.serviceCenterSummaries });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Bookings</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Select a service center to see its bookings.</p>
        </div>
        <Button variant="outline" onClick={onShowRecycleBin}>
          <Trash2 className="h-4 w-4" /> Recycle Bin
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
      ) : isError && !data ? (
        <ErrorState message="Couldn't load service centers." onRetry={() => void refetch()} busy={isFetching} />
      ) : !data?.length ? (
        <p className="rounded-xl bg-gray-50 p-4 text-sm text-[var(--color-text-secondary)]">No active service centers yet.</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((c) => (
            <Card key={c.service_center_id} className="cursor-pointer p-5 transition-shadow hover:shadow-[var(--shadow-lifted)]" onClick={() => onSelect(c.service_center_id)}>
              <div className="flex items-start justify-between gap-2">
                <p className="font-semibold text-[var(--color-text-primary)]">{c.name}</p>
                {c.avg_rating != null && (
                  <span className="flex shrink-0 items-center gap-1 text-sm">
                    <Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />
                    <span className="font-mono-num">{c.avg_rating}</span>
                  </span>
                )}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                <Stat label="Bookings" value={c.bookings} />
                <Stat label="Completed" value={c.completed} tone="success" />
                <Stat label="Pending" value={c.pending} tone="warning" />
                <Stat label="Delayed" value={c.delayed} tone={c.delayed > 0 ? "error" : "neutral"} />
              </div>
              <p className="mt-3 flex items-center gap-1 text-xs font-medium text-[var(--color-primary)]">
                View Bookings <ChevronRight className="h-3.5 w-3.5" />
              </p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "success" | "warning" | "error" | "neutral" }) {
  const toneClass = tone === "success" ? "text-[var(--color-success)]" : tone === "warning" ? "text-amber-600" : tone === "error" ? "text-[var(--color-error)]" : "text-[var(--color-text-primary)]";
  return (
    <div>
      <p className="text-xs text-[var(--color-text-secondary)]">{label}</p>
      <p className={`font-mono-num text-lg font-bold ${toneClass}`}>{value}</p>
    </div>
  );
}

const isClosed = (b: Booking) => b.status === "completed" || b.status === "cancelled";

function CenterBookings({ centerId, onBack, onShowRecycleBin }: { centerId: string; onBack: () => void; onShowRecycleBin: () => void }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const [status, setStatus] = useState("");
  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null);
  const [editingBooking, setEditingBooking] = useState<Booking | null>(null);
  const [cancellingBooking, setCancellingBooking] = useState<Booking | null>(null);
  // Read-only browse (reviews, ratings, every status at a glance) is the
  // default; "Manage" swaps in the SAME queue a manager works from —
  // reassign/self-assign a captain, mark done, cancel, resolve an issue —
  // for whichever center was picked, since an admin has no center of
  // their own for that page to default to.
  const [manageMode, setManageMode] = useState(false);

  // Filters are UI state only — search, dates, sort and paging all run on
  // the server (a busy center's history never fits one fetched page).
  const { search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters([]);
  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(normalisePhoneSearch(search), 300);
  const filterKey = `${status}|${debouncedSearch}|${dateFrom}|${dateTo}|${sortOrder}`;
  useEffect(() => setPage(1), [filterKey]);

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin-center-bookings", centerId, status, debouncedSearch, dateFrom, dateTo, sortOrder, page],
    queryFn: () =>
      bookingApi.forCenter(centerId, {
        page,
        page_size: 50,
        status: status || undefined,
        q: debouncedSearch || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        sort: sortOrder === "oldest" ? "scheduled_asc" : "scheduled_desc",
      }),
    enabled: !manageMode,
    placeholderData: keepPreviousData,
  });

  // Same key as the overview, so the name comes from cache.
  const { data: centers } = useQuery({ queryKey: ["admin-center-summaries"], queryFn: analyticsApi.serviceCenterSummaries });
  const centerName = centers?.find((c) => c.service_center_id === centerId)?.name;

  const reviewsQuery = useQuery({
    queryKey: ["admin-center-bookings-reviews", centerId],
    queryFn: () => reviewApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !manageMode,
  });
  const reviews = reviewsQuery.data;
  // Failed reviews read: every row would say "No Review Yet" — not true.
  const reviewsFailed = reviewsQuery.isError && !reviews;
  const reviewByBooking = new Map((reviews?.data || []).map((r) => [r.booking_id, r]));

  const slabs = toSlabs(data?.data || []).map((slab) => ({ ...slab, id: slab.key }));

  const refreshLists = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-center-bookings", centerId] });
    queryClient.invalidateQueries({ queryKey: ["admin-center-summaries"] });
    queryClient.invalidateQueries({ queryKey: ["admin-recycle-bin"] });
  };

  const deleteMutation = useMutation({
    mutationFn: (id: string) => bookingApi.softDelete(id),
    onSuccess: (res) => {
      refreshLists();
      pushToast({
        tone: "success",
        title: res.deleted_count > 1 ? `${res.deleted_count} bookings moved to the recycle bin` : "Moved to the recycle bin",
      });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const requestDelete = async (b: Booking) => {
    // The shared confirm dialog renders beneath an open drawer, so close it first.
    setSelectedBooking(null);
    const count = slabs.find((s) => s.bookings.some((x) => x.id === b.id))?.vehicleCount ?? 1;
    const visitNote =
      count > 1
        ? `All ${count} vehicles on this visit are deleted together. `
        : b.booking_group_id
          ? "Any other vehicle on the same visit is deleted with it. "
          : "";
    const ok = await confirm({
      title: `Delete ${b.booking_number}?`,
      message: `${visitNote}It moves to the recycle bin — restorable for 30 days, or permanently removable from there.`,
      tone: "danger",
    });
    if (ok) deleteMutation.mutate(b.id);
  };

  const openEdit = (b: Booking) => {
    setSelectedBooking(null);
    setEditingBooking(b);
  };

  if (manageMode) {
    const exitManage = () => {
      // The queue's own edits/deletes don't touch these keys.
      refreshLists();
      setManageMode(false);
    };
    return (
      <div className="space-y-4">
        {/* The admin is working this center's queue AS its manager — same
            screen, but every action is the admin's own (their token, their
            name on the audit trail, attributed to this center server-side).
            Never a login-as: no token of the manager's is ever used. */}
        <div
          role="status"
          className="sticky top-16 z-10 -mx-1 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[14px] border border-[#CFDCF0] bg-[#E8F0FE] px-4 py-3 shadow-[0_8px_20px_-16px_rgba(14,26,51,0.35)]"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white text-[#0A66F0]">
            <ShieldCheck className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-[#0E1A33]">
              Viewing {centerName || "This Center"} As Admin
            </p>
            <p className="text-xs text-[#5F6878]">You act as this center's manager. Every change is logged under your name in Audit logs.</p>
          </div>
          <Button size="sm" variant="outline" onClick={exitManage}>
            <LogOut className="h-3.5 w-3.5" /> Exit Center View
          </Button>
        </div>
        <BookingQueuePage centerIdOverride={centerId} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <button onClick={onBack} className="mb-2 flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
            <ChevronLeft className="h-4 w-4" /> All Service Centers
          </button>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{centerName ? `Bookings · ${centerName}` : "Bookings"}</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Tap a row for full details. To assign a captain or cancel, open the center's queue.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={onShowRecycleBin}>
            <Trash2 className="h-4 w-4" /> Recycle Bin
          </Button>
          <Button variant="outline" onClick={() => setManageMode(true)}>
            <ClipboardEdit className="h-4 w-4" /> Manage This Center's Queue
          </Button>
        </div>
      </div>

      <div className="space-y-3">
        <div className="max-w-xs">
          <Select label="Filter By Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="assigned">Assigned</option>
            <option value="captain_on_the_way">On The Way</option>
            <option value="service_started">In Progress</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
            <option value="rescheduled">Rescheduled</option>
          </Select>
        </div>
        <BookingFilterBar search={search} onSearchChange={setSearch} sortOrder={sortOrder} onSortOrderChange={setSortOrder} dateFrom={dateFrom} onDateFromChange={setDateFrom} dateTo={dateTo} onDateToChange={setDateTo} />
      </div>

      {reviewsFailed && (
        <p role="alert" className="text-sm text-[var(--color-text-secondary)]">
          Couldn't load reviews for these bookings.{" "}
          <button
            type="button"
            className="font-semibold text-[var(--color-primary)] hover:underline disabled:opacity-60"
            disabled={reviewsQuery.isFetching}
            onClick={() => void reviewsQuery.refetch()}
          >
            Try Again
          </button>
        </p>
      )}

      {/* One row per VISIT: several cars wash on one trip are one job to
          dispatch and one bill, so they share a row that lists every car.
          The drawer that opens from it shows each car's own work. */}
      <DataTable<BookingSlab & { id: string }>
        isLoading={isLoading}
        data={slabs}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle={debouncedSearch || dateFrom || dateTo || status ? "No Bookings Match" : "No Bookings"}
        onRowClick={(slab) => setSelectedBooking(slab.primary)}
        columns={[
          {
            header: "Booking #",
            accessor: (slab) => (
              <span className="flex flex-wrap items-center gap-1.5">
                <span className="font-mono-num">{slab.isVisit ? slab.bookings.map((b) => b.booking_number).join(" · ") : slab.primary.booking_number}</span>
                {slab.isVisit && (
                  <span
                    title="Several vehicles wash on one visit — one trip, one slot, one payment"
                    className="rounded-full bg-gray-900 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white"
                  >
                    {slab.vehicleCount} Vehicles
                  </span>
                )}
              </span>
            ),
          },
          { header: "Customer", accessor: (slab) => slab.primary.customer_name || "—" },
          {
            header: "Vehicle & Service",
            accessor: (slab) =>
              slab.isVisit ? (
                <ul className="space-y-0.5 text-xs">
                  {slab.bookings.map((b, i) => (
                    <li key={b.id}>
                      <span className="font-mono-num mr-1 text-gray-400">{i + 1}.</span>
                      {toTitle(b.vehicle_type_name) || vehicleLabel(b) || "—"} <span className="text-[var(--color-text-secondary)]">— {toTitle(bookingServiceLabel(b, "—"))}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <span>
                  {toTitle(slab.primary.vehicle_type_name) || vehicleLabel(slab.primary)}
                  <span className="block text-xs text-[var(--color-text-secondary)]">{toTitle(slab.serviceLabel)}</span>
                </span>
              ),
          },
          {
            header: "Added On Site",
            accessor: (slab) => {
              const added = slab.bookings.flatMap((b) => (b as StaffBooking).added_services || []);
              if (!added.length) return <span className="text-gray-300">—</span>;
              const by = added[added.length - 1];
              return (
                <span className="text-xs" data-testid="admin-added-on-site">
                  <span className="font-medium">{added.map((a) => `${toTitle(a.name)}${a.qty > 1 ? ` ×${a.qty}` : ""}`).join(", ")}</span>
                  <span className="block text-[var(--color-text-secondary)]">
                    Added On Site By {by.by_name || toTitle(by.role)}
                    {by.role && by.by_name ? ` (${toTitle(by.role)})` : ""}
                    {by.stage === "completed" ? " · After The Wash" : ""}
                  </span>
                </span>
              );
            },
          },
          { header: "Slot", accessor: (slab) => `${format(slab.primary.scheduled_date)} · ${formatSlot(slab.primary.scheduled_slot)}` },
          { header: "Amount", accessor: (slab) => <span className="font-mono-num">₹{slab.totalAmount}</span> },
          { header: "Priority", accessor: (slab) => <Badge tone={slab.primary.priority === "high" ? "error" : "neutral"}>{toTitle(slab.primary.priority)}</Badge> },
          {
            header: "Status",
            accessor: (slab) => (
              <div className="flex flex-col items-start gap-1">
                <StatusBadge status={slab.status} />
                {slab.bookings.some((x) => x.payment_status === "partially_paid") && (
                  <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-800">Part Paid</span>
                )}
                {slab.bookings.filter((x) => (x as StaffBooking).customer_edited_at).slice(0, 1).map((x) => <CustomerEditedChip key={x.id} booking={x as StaffBooking} />)}
              </div>
            ),
          },
          {
            header: "Review",
            accessor: (slab) => {
              // Each car is rated on its own; the row shows the visit's
              // average when more than one has been rated.
              const ratings = slab.bookings
                .map((b) => reviewByBooking.get(b.id))
                .filter(Boolean)
                .map((r) => r!.captain_rating ?? r!.service_rating ?? r!.rating)
                .filter((v): v is number => typeof v === "number");
              if (!ratings.length)
                return <span className="text-xs text-[var(--color-text-secondary)]">{reviewsFailed ? "—" : "No Review Yet"}</span>;
              const rating = ratings.reduce((a, b) => a + b, 0) / ratings.length;
              return (
                <span className="flex items-center gap-1 text-xs">
                  <Star className="h-3 w-3 fill-[var(--color-secondary)] text-[var(--color-secondary)]" /> {rating.toFixed(1)}
                  {ratings.length > 1 ? <span className="text-[var(--color-text-secondary)]">· {ratings.length} cars</span> : null}
                </span>
              );
            },
          },
          {
            header: "",
            accessor: (slab) => {
              // Edit is refused server-side once a car is closed; any open car edits the whole visit.
              const editable = slab.bookings.find((b) => !isClosed(b));
              return (
                <div className="flex gap-1.5 whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!editable}
                    title={editable ? "Edit notes and alternate contact" : "A completed or cancelled booking can't be edited"}
                    onClick={() => editable && openEdit(editable)}
                  >
                    <Pencil className="h-3.5 w-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    title="Move to the recycle bin"
                    isLoading={deleteMutation.isPending && deleteMutation.variables === slab.primary.id}
                    onClick={() => requestDelete(slab.primary)}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" /> Delete
                  </Button>
                </div>
              );
            },
          },
        ]}
      />

      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}

      <BookingDetailDrawer
        booking={selectedBooking}
        onClose={() => setSelectedBooking(null)}
        centerName={centerName}
        onEdit={openEdit}
        onDelete={requestDelete}
        onCancel={(b) => {
          setSelectedBooking(null);
          setCancellingBooking(b);
        }}
      />
      <StaffCancelDialog booking={cancellingBooking} onClose={() => setCancellingBooking(null)} />
      <EditBookingModal
        booking={editingBooking}
        onClose={() => setEditingBooking(null)}
        onSaved={() => {
          queryClient.invalidateQueries({ queryKey: ["admin-center-bookings", centerId] });
          if (editingBooking?.booking_group_id) queryClient.invalidateQueries({ queryKey: ["booking-group", editingBooking.booking_group_id] });
          pushToast({ tone: "success", title: "Booking updated" });
        }}
      />
    </div>
  );
}

/** Soft-deleted bookings, platform-wide (deletion isn't center-scoped —
 *  an admin can delete from any center). Reversible for 30 days (see
 *  main.py's purge sweep) via Restore; "Force delete permanently" is the
 *  one irreversible action here, gated by its own confirmation plus the
 *  money-attached warning chips already visible inline on the row. */
function RecycleBinView({ onBack, backLabel }: { onBack: () => void; backLabel: string }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-recycle-bin"] });
    queryClient.invalidateQueries({ queryKey: ["admin-center-bookings"] });
    queryClient.invalidateQueries({ queryKey: ["admin-center-summaries"] });
  };

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["admin-recycle-bin"],
    queryFn: () => bookingApi.recycleBin({ page: 1, page_size: 100 }),
    // Deletes from the manager queue (admin "Manage" mode) don't invalidate this key.
    refetchOnMount: "always",
  });

  const restoreMutation = useMutation({
    mutationFn: (id: string) => bookingApi.restore(id),
    onSuccess: () => {
      invalidate();
      pushToast({ tone: "success", title: "Restored" });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  // Deliberately NOT a plain useMutation(force: true) — that would send
  // force on every click, making the backend's money-attached gate
  // (captain wallet / paid online payment / complaint) unreachable from
  // the product entirely. Try un-forced first; only on that specific
  // refusal does this escalate to a second, more explicit confirmation
  // before retrying with force — matching "admin FORCEFULLY deleted" as
  // a deliberate override, not the default path.
  const [permanentlyDeletingId, setPermanentlyDeletingId] = useState<string | null>(null);
  const permanentlyDelete = async (id: string, label: string) => {
    if (
      !(await confirm({
        title: `Permanently Delete ${label}?`,
        message: "This cannot be undone — the booking and everything tied to it (status history, notifications, review, GPS trail) is gone for good.",
        tone: "danger",
      }))
    )
      return;
    setPermanentlyDeletingId(id);
    try {
      await bookingApi.permanentlyDelete(id, false);
      invalidate();
      pushToast({ tone: "success", title: "Permanently deleted" });
    } catch (err) {
      const message = getErrorMessage(err);
      if (!message.includes("Use force to delete anyway")) {
        pushToast({ tone: "error", title: message });
        return;
      }
      const forced = await confirm({
        title: "Money Is Attached To This Booking",
        message: `${message} Deleting it here does not reverse any payment, payout, or refund — that stays a manual (or Razorpay) matter. Force delete anyway?`,
        tone: "danger",
        confirmLabel: "Force Delete",
      });
      if (!forced) return;
      try {
        await bookingApi.permanentlyDelete(id, true);
        invalidate();
        pushToast({ tone: "success", title: "Permanently deleted" });
      } catch (err2) {
        pushToast({ tone: "error", title: getErrorMessage(err2) });
      }
    } finally {
      setPermanentlyDeletingId(null);
    }
  };

  // Group multi-car visits into one row, same as the browse table above —
  // without this a 2-car visit's soft-delete showed as two identical-
  // looking rows, and restoring/deleting either one (both already expand
  // to the whole group server-side) left the OTHER row stale until refetch.
  const slabs = toSlabs(data?.data || []).map((slab) => ({ ...slab, id: slab.key }));

  const flagChips = (b: Booking) => {
    const flags = b.deleted_flags;
    if (!flags) return null;
    const labels = [
      flags.had_captain_earning && "Captain Already Paid",
      flags.had_paid_online_payment && "Customer Paid Online",
      flags.had_complaints && "Has A Complaint",
    ].filter(Boolean) as string[];
    if (!labels.length) return null;
    return (
      <div className="mt-1 flex flex-wrap gap-1">
        {labels.map((l) => (
          <span key={l} className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
            <AlertTriangle className="h-2.5 w-2.5" /> {l}
          </span>
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div>
        <button onClick={onBack} className="mb-2 flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
          <ChevronLeft className="h-4 w-4" /> {backLabel}
        </button>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Recycle Bin</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Deleted bookings stay here for 30 days before they're removed automatically — restore one, or delete it permanently now.
        </p>
      </div>

      <DataTable<BookingSlab & { id: string }>
        isLoading={isLoading}
        data={slabs}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle="Recycle Bin Is Empty"
        columns={[
          {
            header: "Booking",
            accessor: (slab) => (
              <div>
                <span className="font-mono-num font-medium text-black">
                  {slab.isVisit ? slab.bookings.map((b) => b.booking_number).join(" · ") : slab.primary.booking_number}
                </span>
                <p className="text-xs text-[var(--color-text-secondary)]">{slab.primary.customer_name || "—"}</p>
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {slab.isVisit ? slab.bookings.map(bookingCarAndService).filter(Boolean).join(" + ") : bookingCarAndService(slab.primary)}
                </p>
                {flagChips(slab.primary)}
              </div>
            ),
          },
          { header: "Amount", accessor: (slab) => <span className="font-mono-num">₹{slab.totalAmount}</span> },
          { header: "Status When Deleted", accessor: (slab) => <StatusBadge status={slab.status} /> },
          { header: "Deleted", accessor: (slab) => (slab.primary.deleted_at ? format(slab.primary.deleted_at) : "—") },
          {
            header: "Purge In",
            accessor: (slab) => (
              <span className={slab.primary.days_remaining != null && slab.primary.days_remaining <= 3 ? "font-semibold text-[var(--color-error)]" : ""}>
                {slab.primary.days_remaining != null ? `${slab.primary.days_remaining} day${slab.primary.days_remaining === 1 ? "" : "s"}` : "—"}
              </span>
            ),
          },
          {
            header: "",
            accessor: (slab) => (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  isLoading={restoreMutation.isPending && restoreMutation.variables === slab.primary.id}
                  onClick={() => restoreMutation.mutate(slab.primary.id)}
                >
                  <RotateCcw className="h-3.5 w-3.5" /> Restore
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={permanentlyDeletingId === slab.primary.id}
                  onClick={() =>
                    permanentlyDelete(slab.primary.id, slab.isVisit ? slab.bookings.map((b) => b.booking_number).join(", ") : slab.primary.booking_number)
                  }
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" /> Delete Permanently
                </Button>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
