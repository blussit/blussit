import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarPlus, ChevronRight, Gift, Search, SlidersHorizontal, Sparkles, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { reviewApi } from "../../api/engagement";
import { Button, EmptyState, Input, PageLoader, Select, StatusBadge } from "../../components/ui";
import { useBookingFilters, type SortOrder } from "../../lib/useBookingFilters";
import { serviceImage } from "../../components/public/landing/shared";
import { format } from "../../lib/date";

const STATUS_OPTIONS = ["", "pending", "assigned", "captain_on_the_way", "service_started", "completed", "cancelled", "rescheduled"];

export default function MyBookingsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["my-bookings", status],
    queryFn: () => bookingApi.myBookings({ status: status || undefined, page: 1, page_size: 100 }),
  });

  // One fetch of the customer's own reviews, mapped per booking — powers
  // the rate-it-right-here stars on completed rows below.
  const { data: myReviews } = useQuery({ queryKey: ["my-reviews"], queryFn: reviewApi.mine });
  const reviewByBookingId = new Map((myReviews || []).map((r) => [r.booking_id, r]));

  const { filtered, search, setSearch, sortOrder, setSortOrder, dateFrom, setDateFrom, dateTo, setDateTo } = useBookingFilters(
    data?.data || []
  );
  const activeFilters = (status ? 1 : 0) + (sortOrder !== "newest" ? 1 : 0) + (dateFrom ? 1 : 0) + (dateTo ? 1 : 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Bookings</p>
          <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">My bookings</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Track, reschedule, or review your services.</p>
        </div>
        <Button onClick={() => navigate("/app/book")}>
          <CalendarPlus className="h-4 w-4" /> New booking
        </Button>
      </div>

      {/* One search box + one Filters button — everything else lives
          inside the panel, with a count chip when filters are active. */}
      <div className="space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-300" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search booking #…"
              className="w-full rounded-xl border border-[#F3E5B5] bg-white py-2.5 pl-10 pr-3 text-sm outline-none transition-colors placeholder:text-gray-400 focus:border-[#E8A900]"
            />
          </div>
          <button
            type="button"
            onClick={() => setFiltersOpen((v) => !v)}
            className={`flex shrink-0 items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-semibold transition-colors ${
              filtersOpen || activeFilters > 0
                ? "border-[#E8A900] bg-[#FFF4CD] text-black"
                : "border-[#F3E5B5] bg-white text-gray-600 hover:border-[#E8A900]/50"
            }`}
          >
            <SlidersHorizontal className="h-4 w-4" />
            Filters
            {activeFilters > 0 && (
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#E8A900] text-[11px] font-bold text-white">
                {activeFilters}
              </span>
            )}
          </button>
        </div>

        {filtersOpen && (
          <div className="rounded-2xl border border-[#F3E5B5] bg-white p-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s ? s.replace(/_/g, " ") : "All statuses"}
                  </option>
                ))}
              </Select>
              <Select label="Sort" value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)}>
                <option value="newest">Newest first</option>
                <option value="oldest">Oldest first</option>
              </Select>
              <Input label="From" type="date" value={dateFrom} max={dateTo || undefined} onChange={(e) => setDateFrom(e.target.value)} />
              <Input label="To" type="date" value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} />
            </div>
            {activeFilters > 0 && (
              <button
                type="button"
                onClick={() => {
                  setStatus("");
                  setSortOrder("newest");
                  setDateFrom("");
                  setDateTo("");
                }}
                className="mt-3 text-xs font-bold text-black hover:underline"
              >
                Clear all filters
              </button>
            )}
          </div>
        )}
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !filtered.length ? (
        <EmptyState
          title="No bookings found"
          description="Try a different filter or book your first service."
          action={<Button onClick={() => navigate("/app/book")}>Book now</Button>}
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((b, i) => {
            // Same real shoot photo the landing uses for this service —
            // matched by the first service's name; the size stays 44px.
            const firstService = (b.combo_name || b.service_names?.[0] || "").replace(/\s*×\d+$/, "");
            const isPlanBooking = b.payment_method === "subscription" || !!b.subscription_id;
            return (
              <button
                key={b.id}
                onClick={() => navigate(`/app/bookings/${b.id}`)}
                className="flex w-full items-center gap-4 rounded-2xl border border-[#F3E5B5] bg-white p-4 text-left transition-all hover:border-[#E8A900]/50 hover:shadow-[0_10px_24px_rgba(60,40,0,0.07)] sm:p-5"
              >
                {firstService ? (
                  <img
                    src={serviceImage({ name: firstService }, i)}
                    alt={firstService}
                    className="hidden h-11 w-11 shrink-0 rounded-xl object-cover sm:block"
                  />
                ) : (
                  <span className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black sm:flex">
                    <Sparkles className="h-5 w-5" />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <p className="font-mono-num text-sm font-bold text-black">{b.booking_number}</p>
                    <p className="text-sm text-gray-600">
                      {format(b.scheduled_date)} · {b.scheduled_slot}
                    </p>
                    {isPlanBooking && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-gray-600">
                        <Gift className="h-2.5 w-2.5" /> Plan
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-gray-400">
                    {b.combo_name || b.service_names?.join(", ") || "Service"}
                    {b.vehicle_registration_number ? ` · ${b.vehicle_registration_number}` : ""}
                  </p>
                  {/* Completed bookings carry their rating right on the
                      row — filled stars for what they gave, or empty
                      "Rate" stars that jump straight into the rating
                      window (the detail page auto-opens it when the
                      booking is completed and unrated). */}
                  {b.status === "completed" && (() => {
                    const r = reviewByBookingId.get(b.id);
                    const given = r ? r.service_rating ?? r.captain_rating ?? r.rating ?? 0 : 0;
                    return (
                      <span className="mt-1.5 flex items-center gap-1" title={r ? `You rated ${given}/5` : "Rate this service"}>
                        {Array.from({ length: 5 }).map((_, si) => (
                          <Star key={si} className={`h-3.5 w-3.5 ${si < given ? "fill-[#E8A900] text-[#E8A900]" : "text-gray-300"}`} />
                        ))}
                        <span className="ml-1 text-[11px] font-medium text-gray-400">{r ? `You rated ${given}/5` : "Tap to rate"}</span>
                      </span>
                    );
                  })()}
                </div>
                <span className="hidden font-mono-num text-sm font-bold text-black md:block">₹{b.total_amount}</span>
                <StatusBadge status={b.status} />
                <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
