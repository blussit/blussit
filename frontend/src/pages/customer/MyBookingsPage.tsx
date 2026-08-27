import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarPlus } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { Button, DataTable, Select, StatusBadge } from "../../components/ui";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { format } from "../../lib/date";
import type { Booking } from "../../types";

const STATUS_OPTIONS = ["", "pending", "assigned", "captain_on_the_way", "service_started", "completed", "cancelled", "rescheduled"];

export default function MyBookingsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["my-bookings", status],
    queryFn: () => bookingApi.myBookings({ status: status || undefined, page: 1, page_size: 100 }),
  });

  const { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters(
    data?.data || []
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">My bookings</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Track, reschedule, or review your services.</p>
        </div>
        <Button onClick={() => navigate("/app/book")}>
          <CalendarPlus className="h-4 w-4" /> New booking
        </Button>
      </div>

      <div className="space-y-3">
        <div className="max-w-xs">
          <Select label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s ? s.replace(/_/g, " ") : "All statuses"}
              </option>
            ))}
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
          searchPlaceholder="Booking #"
        />
      </div>

      <DataTable<Booking>
        isLoading={isLoading}
        data={filtered}
        emptyTitle="No bookings found"
        emptyDescription="Try a different filter or book your first service."
        onRowClick={(b) => navigate(`/app/bookings/${b.id}`)}
        columns={[
          { header: "Booking #", accessor: (b) => <span className="font-mono-num">{b.booking_number}</span> },
          { header: "Date", accessor: (b) => `${format(b.scheduled_date)} · ${b.scheduled_slot}` },
          { header: "Amount", accessor: (b) => <span className="font-mono-num">₹{b.total_amount}</span> },
          { header: "Status", accessor: (b) => <StatusBadge status={b.status} /> },
        ]}
      />
    </div>
  );
}
