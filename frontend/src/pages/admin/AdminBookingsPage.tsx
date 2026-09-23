import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronLeft, ChevronRight, ClipboardEdit, RotateCcw, Star, Trash2 } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { analyticsApi } from "../../api/admin";
import { reviewApi } from "../../api/engagement";
import { Badge, Button, Card, DataTable, Select, StatusBadge } from "../../components/ui";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import BookingQueuePage from "../manager/BookingQueuePage";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatSlot } from "../../lib/date";
import { toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import { vehicleLabel } from "../../lib/constants";
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

  if (showRecycleBin) return <RecycleBinView onBack={() => setShowRecycleBin(false)} />;
  return selectedCenterId ? (
    <CenterBookings centerId={selectedCenterId} onBack={() => setSelectedCenterId(null)} />
  ) : (
    <ServiceCenterOverview onSelect={setSelectedCenterId} onShowRecycleBin={() => setShowRecycleBin(true)} />
  );
}

function ServiceCenterOverview({ onSelect, onShowRecycleBin }: { onSelect: (centerId: string) => void; onShowRecycleBin: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["admin-center-summaries"], queryFn: analyticsApi.serviceCenterSummaries });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Bookings</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Select a service center to see its bookings.</p>
        </div>
        <Button variant="outline" onClick={onShowRecycleBin}>
          <Trash2 className="h-4 w-4" /> Recycle bin
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
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
                View bookings <ChevronRight className="h-3.5 w-3.5" />
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

function CenterBookings({ centerId, onBack }: { centerId: string; onBack: () => void }) {
  const [status, setStatus] = useState("");
  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null);
  // Read-only browse (reviews, ratings, every status at a glance) is the
  // default; "Manage" swaps in the SAME queue a manager works from —
  // reassign/self-assign a captain, mark done, cancel, resolve an issue —
  // for whichever center was picked, since an admin has no center of
  // their own for that page to default to.
  const [manageMode, setManageMode] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["admin-center-bookings", centerId, status],
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100, status: status || undefined }),
    enabled: !manageMode,
  });

  const { data: reviews } = useQuery({
    queryKey: ["admin-center-bookings-reviews", centerId],
    queryFn: () => reviewApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !manageMode,
  });
  const reviewByBooking = new Map((reviews?.data || []).map((r) => [r.booking_id, r]));

  const { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters(data?.data || []);

  if (manageMode) {
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button onClick={() => setManageMode(false)} className="flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
            <ChevronLeft className="h-4 w-4" /> Back to browse
          </button>
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
            <ChevronLeft className="h-4 w-4" /> All service centers
          </button>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Bookings</h1>
        </div>
        <Button variant="outline" onClick={() => setManageMode(true)}>
          <ClipboardEdit className="h-4 w-4" /> Manage this center's queue
        </Button>
      </div>

      <div className="space-y-3">
        <div className="max-w-xs">
          <Select label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="assigned">Assigned</option>
            <option value="captain_on_the_way">On the way</option>
            <option value="service_started">In progress</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
            <option value="rescheduled">Rescheduled</option>
          </Select>
        </div>
        <BookingFilterBar search={search} onSearchChange={setSearch} sortOrder={sortOrder} onSortOrderChange={setSortOrder} dateFrom={dateFrom} onDateFromChange={setDateFrom} dateTo={dateTo} onDateToChange={setDateTo} />
      </div>

      {/* One row per VISIT: several cars washed on one trip are one job to
          dispatch and one bill, so they share a row that lists every car.
          The drawer that opens from it shows each car's own work. */}
      <DataTable<BookingSlab & { id: string }>
        isLoading={isLoading}
        data={toSlabs(filtered).map((slab) => ({ ...slab, id: slab.key }))}
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
                    title="Several vehicles washed on one visit — one trip, one slot, one payment"
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
                <span>
                  {vehicleLabel(slab.primary)}
                  <span className="block text-xs text-[var(--color-text-secondary)]">{slab.serviceLabel}</span>
                </span>
              ),
          },
          { header: "Slot", accessor: (slab) => `${format(slab.primary.scheduled_date)} · ${formatSlot(slab.primary.scheduled_slot)}` },
          { header: "Amount", accessor: (slab) => <span className="font-mono-num">₹{slab.totalAmount}</span> },
          { header: "Priority", accessor: (slab) => <Badge tone={slab.primary.priority === "high" ? "error" : "neutral"}>{slab.primary.priority}</Badge> },
          { header: "Status", accessor: (slab) => <StatusBadge status={slab.status} /> },
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
              if (!ratings.length) return <span className="text-xs text-[var(--color-text-secondary)]">No review yet</span>;
              const rating = ratings.reduce((a, b) => a + b, 0) / ratings.length;
              return (
                <span className="flex items-center gap-1 text-xs">
                  <Star className="h-3 w-3 fill-[var(--color-secondary)] text-[var(--color-secondary)]" /> {rating.toFixed(1)}
                  {ratings.length > 1 ? <span className="text-[var(--color-text-secondary)]">· {ratings.length} cars</span> : null}
                </span>
              );
            },
          },
        ]}
      />

      <BookingDetailDrawer booking={selectedBooking} onClose={() => setSelectedBooking(null)} />
    </div>
  );
}

/** Soft-deleted bookings, platform-wide (deletion isn't center-scoped —
 *  an admin can delete from any center). Reversible for 30 days (see
 *  main.py's purge sweep) via Restore; "Force delete permanently" is the
 *  one irreversible action here, gated by its own confirmation plus the
 *  money-attached warning chips already visible inline on the row. */
function RecycleBinView({ onBack }: { onBack: () => void }) {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-recycle-bin"] });

  const { data, isLoading } = useQuery({
    queryKey: ["admin-recycle-bin"],
    queryFn: () => bookingApi.recycleBin({ page: 1, page_size: 100 }),
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
        title: `Permanently delete ${label}?`,
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
        title: "Money is attached to this booking",
        message: `${message} Deleting it here does not reverse any payment, payout, or refund — that stays a manual (or Razorpay) matter. Force delete anyway?`,
        tone: "danger",
        confirmLabel: "Force delete",
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
      flags.had_captain_earning && "Captain already paid",
      flags.had_paid_online_payment && "Customer paid online",
      flags.had_complaints && "Has a complaint",
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
          <ChevronLeft className="h-4 w-4" /> All service centers
        </button>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Recycle bin</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Deleted bookings stay here for 30 days before they're removed automatically — restore one, or delete it permanently now.
        </p>
      </div>

      <DataTable<BookingSlab & { id: string }>
        isLoading={isLoading}
        data={slabs}
        emptyTitle="Recycle bin is empty"
        columns={[
          {
            header: "Booking",
            accessor: (slab) => (
              <div>
                <span className="font-mono-num font-medium text-black">
                  {slab.isVisit ? slab.bookings.map((b) => b.booking_number).join(" · ") : slab.primary.booking_number}
                </span>
                <p className="text-xs text-[var(--color-text-secondary)]">{slab.primary.customer_name || "—"}</p>
                {flagChips(slab.primary)}
              </div>
            ),
          },
          { header: "Amount", accessor: (slab) => <span className="font-mono-num">₹{slab.totalAmount}</span> },
          { header: "Status when deleted", accessor: (slab) => <StatusBadge status={slab.status} /> },
          { header: "Deleted", accessor: (slab) => (slab.primary.deleted_at ? format(slab.primary.deleted_at) : "—") },
          {
            header: "Purge in",
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
                <Button size="sm" variant="outline" isLoading={restoreMutation.isPending} onClick={() => restoreMutation.mutate(slab.primary.id)}>
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
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" /> Delete permanently
                </Button>
              </div>
            ),
          },
        ]}
      />
    </div>
  );
}
