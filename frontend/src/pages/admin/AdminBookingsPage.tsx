import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { analyticsApi } from "../../api/admin";
import { reviewApi } from "../../api/engagement";
import { Badge, Card, DataTable, Select, StatusBadge } from "../../components/ui";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { format } from "../../lib/date";
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

  return selectedCenterId ? (
    <CenterBookings centerId={selectedCenterId} onBack={() => setSelectedCenterId(null)} />
  ) : (
    <ServiceCenterOverview onSelect={setSelectedCenterId} />
  );
}

function ServiceCenterOverview({ onSelect }: { onSelect: (centerId: string) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["admin-center-summaries"], queryFn: analyticsApi.serviceCenterSummaries });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Bookings</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Select a service center to see its bookings.</p>
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

  const { data, isLoading } = useQuery({
    queryKey: ["admin-center-bookings", centerId, status],
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100, status: status || undefined }),
  });

  const { data: reviews } = useQuery({ queryKey: ["admin-center-bookings-reviews", centerId], queryFn: () => reviewApi.forCenter(centerId, { page: 1, page_size: 100 }) });
  const reviewByBooking = new Map((reviews?.data || []).map((r) => [r.booking_id, r]));

  const { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters(data?.data || []);

  return (
    <div className="space-y-6">
      <div>
        <button onClick={onBack} className="mb-2 flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
          <ChevronLeft className="h-4 w-4" /> All service centers
        </button>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Bookings</h1>
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

      <DataTable<Booking>
        isLoading={isLoading}
        data={filtered}
        emptyTitle="No bookings"
        onRowClick={(b) => setSelectedBooking(b)}
        columns={[
          { header: "Booking #", accessor: (b) => <span className="font-mono-num">{b.booking_number}</span> },
          { header: "Customer", accessor: (b) => b.customer_name || "—" },
          { header: "Vehicle", accessor: (b) => (b.vehicle_snapshot ? `${b.vehicle_snapshot.brand} ${b.vehicle_snapshot.model}` : "—") },
          { header: "Service", accessor: (b) => b.combo_name || b.service_names?.join(", ") || "—" },
          { header: "Slot", accessor: (b) => `${format(b.scheduled_date)} · ${b.scheduled_slot}` },
          { header: "Priority", accessor: (b) => <Badge tone={b.priority === "high" ? "error" : "neutral"}>{b.priority}</Badge> },
          { header: "Status", accessor: (b) => <StatusBadge status={b.status} /> },
          {
            header: "Review",
            accessor: (b) => {
              const r = reviewByBooking.get(b.id);
              if (!r) return <span className="text-xs text-[var(--color-text-secondary)]">No review yet</span>;
              const rating = r.captain_rating ?? r.rating ?? 0;
              return (
                <span className="flex items-center gap-1 text-xs">
                  <Star className="h-3 w-3 fill-[var(--color-secondary)] text-[var(--color-secondary)]" /> {rating.toFixed(1)}
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
