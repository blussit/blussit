import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { bookingApi } from "../../api/booking";
import { Button, DataTable, Select, StatusBadge } from "../../components/ui";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { format } from "../../lib/date";
import type { Booking } from "../../types";

export default function AdminBookingsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");

  // Fetched once, unpaginated (up to the backend's max page size) — search,
  // sort, and date-range filtering all happen client-side on this set, same
  // pattern as the manager's booking queue.
  const { data, isLoading } = useQuery({
    queryKey: ["admin-bookings", status],
    queryFn: () => bookingApi.all({ status: status || undefined, page: 1, page_size: 100 }),
  });

  const { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters(
    data?.data || []
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">All bookings</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Platform-wide booking oversight.</p>
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
      </div>

      <DataTable<Booking>
        isLoading={isLoading}
        data={filtered}
        emptyTitle="No bookings found"
        columns={[
          { header: "Booking #", accessor: (b) => <span className="font-mono-num">{b.booking_number}</span> },
          { header: "Date", accessor: (b) => `${format(b.scheduled_date)} · ${b.scheduled_slot}` },
          { header: "Amount", accessor: (b) => <span className="font-mono-num">₹{b.total_amount}</span> },
          { header: "Status", accessor: (b) => <StatusBadge status={b.status} /> },
          { header: "", accessor: (b) => <Button size="sm" variant="outline" onClick={() => navigate(`/admin/bookings/${b.id}`)}>View</Button> },
        ]}
      />
    </div>
  );
}
