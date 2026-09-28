import { useMemo, useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarPlus, ChevronRight, Gift, RotateCcw, Search, SlidersHorizontal, Sparkles, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { reviewApi } from "../../api/engagement";
import { Button, EmptyState, Input, PageLoader, Select, StatusBadge } from "../../components/ui";
import { serviceImage } from "../../components/public/landing/shared";
import { formatShortDate, formatSlot } from "../../lib/date";
import { toSlabs, visitServiceLabel } from "../../lib/bookingGroups";
import { vehicleLabel } from "../../lib/constants";
import { customerStatusLabel } from "../../lib/customerStatus";
import type { Booking } from "../../types";

const PAGE_SIZE = 20;
const STATUS_OPTIONS = ["", "awaiting_payment", "pending", "assigned", "captain_on_the_way", "service_started", "completed", "cancelled", "rescheduled"];

/**
 * The customer's bookings, newest booked first (the server's order), 20 at
 * a time with "Load more". Status filters on the server; search and dates
 * narrow what's loaded so far — and say so while older pages remain.
 */
export default function MyBookingsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState(""); // "YYYY-MM-DD"
  const [dateTo, setDateTo] = useState("");

  const query = useInfiniteQuery({
    queryKey: ["my-bookings", "list", status],
    queryFn: ({ pageParam }) => bookingApi.myBookings({ status: status || undefined, page: pageParam, page_size: PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.meta.page < last.meta.total_pages ? last.meta.page + 1 : undefined),
  });
  const total = query.data?.pages[0]?.meta.total ?? 0;

  // Every loaded page as one list — deduped, since a booking made while
  // paging shifts the server's pages by one.
  const loaded = useMemo(() => {
    const seen = new Set<string>();
    const all: Booking[] = [];
    for (const page of query.data?.pages ?? []) {
      for (const b of page.data) {
        if (seen.has(b.id)) continue;
        seen.add(b.id);
        all.push(b);
      }
    }
    return all;
  }, [query.data]);

  // The reviews for exactly the completed bookings on screen (the plain
  // list stops at the newest 100) — powers the rating stars on those rows.
  const completedIds = useMemo(() => loaded.filter((b) => b.status === "completed").map((b) => b.id), [loaded]);
  const { data: myReviews } = useQuery({
    queryKey: ["my-reviews", "for", completedIds.join(",")],
    queryFn: () => reviewApi.mine(completedIds),
    enabled: completedIds.length > 0,
    placeholderData: keepPreviousData,
  });
  const reviewByBookingId = new Map((myReviews || []).map((r) => [r.booking_id, r]));

  const q = search.trim().toLowerCase();
  const narrowing = !!q || !!dateFrom || !!dateTo;
  // Grouped BEFORE filtering, so a multi-car visit loaded across two pages
  // is still one card, and a match on any car shows the whole visit.
  const slabs = useMemo(() => {
    return toSlabs(loaded).filter((slab) => {
      if (q) {
        const hay = slab.bookings
          .flatMap((b) => [b.booking_number, b.combo_name || "", ...(b.service_names || [])])
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      const day = slab.primary.scheduled_date.slice(0, 10);
      if (dateFrom && day < dateFrom) return false;
      if (dateTo && day > dateTo) return false;
      return true;
    });
  }, [loaded, q, dateFrom, dateTo]);

  const activeFilters = (status ? 1 : 0) + (dateFrom ? 1 : 0) + (dateTo ? 1 : 0);
  const more = !!query.hasNextPage;

  const loadMore = (
    <div className="flex flex-col items-center gap-1.5 pt-1">
      <Button variant="outline" isLoading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
        Load more
      </Button>
      <p className="text-xs text-gray-500">
        Showing {loaded.length} of {total}
      </p>
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-2xl font-bold text-black">My bookings</h1>
        <Button variant="info" onClick={() => navigate("/app/book")}>
          <CalendarPlus className="h-4 w-4" /> Book a service
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
              placeholder="Booking # or service"
              aria-label="Search bookings"
              className="w-full rounded-xl border border-[#F3E5B5] bg-white py-2.5 pl-10 pr-3 text-sm outline-none transition-colors placeholder:text-gray-400 focus:border-black"
            />
          </div>
          <button
            type="button"
            onClick={() => setFiltersOpen((v) => !v)}
            aria-expanded={filtersOpen}
            className={`flex shrink-0 items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-semibold transition-colors ${
              filtersOpen || activeFilters > 0
                ? "border-black bg-[#FFF4CD] text-black"
                : "border-[#F3E5B5] bg-white text-gray-600 hover:border-gray-300"
            }`}
          >
            <SlidersHorizontal className="h-4 w-4" />
            Filters
            {activeFilters > 0 && (
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-black text-[11px] font-bold text-white">
                {activeFilters}
              </span>
            )}
          </button>
        </div>

        {filtersOpen && (
          <div className="rounded-2xl border border-[#F3E5B5] bg-white p-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s ? customerStatusLabel(s) || s.replace(/_/g, " ") : "All statuses"}
                  </option>
                ))}
              </Select>
              <div className="grid grid-cols-2 gap-3 sm:col-span-2">
                <Input label="From" type="date" value={dateFrom} max={dateTo || undefined} onChange={(e) => setDateFrom(e.target.value)} />
                <Input label="To" type="date" value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} />
              </div>
            </div>
            {activeFilters > 0 && (
              <button
                type="button"
                onClick={() => {
                  setStatus("");
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

        {/* Search and dates only look at what's loaded — say so while there's more. */}
        {narrowing && more && (
          <p className="text-xs text-gray-500">
            Searching your latest {loaded.length} bookings.{" "}
            <button type="button" onClick={() => void query.fetchNextPage()} className="font-semibold text-black underline underline-offset-2">
              Load older ones
            </button>
          </p>
        )}
      </div>

      {query.isLoading ? (
        <PageLoader />
      ) : query.isError ? (
        <EmptyState
          title="Couldn't load your bookings"
          action={
            <Button variant="outline" onClick={() => void query.refetch()}>
              Try again
            </Button>
          }
        />
      ) : !slabs.length ? (
        <>
          <EmptyState
            title={narrowing && loaded.length ? "No matches in what's loaded" : "No bookings found"}
            action={
              <Button variant="info" onClick={() => navigate("/app/book")}>
                Book a service
              </Button>
            }
          />
          {narrowing && more && loadMore}
        </>
      ) : (
        <div className="space-y-3">
          {slabs.map((slab, i) => {
            const b = slab.primary;
            const serviceLabel = visitServiceLabel(slab);
            // Same real shoot photo the landing uses for this service —
            // matched by the first service's name; the size stays 44px.
            const firstService = (b.combo_name || b.service_names?.[0] || "").replace(/\s*×\d+$/, "");
            const isPlanBooking = b.payment_method === "subscription" || !!b.subscription_id;
            // A finished or cancelled booking is the one a customer wants
            // back — the shortcut sits on the row, and the wizard replays
            // the car, service and address so only the date is left.
            const canRebook = slab.status === "completed" || slab.status === "cancelled";
            const openDetail = () => navigate(`/app/bookings/${b.id}`);
            const review = !slab.isVisit && b.status === "completed" ? reviewByBookingId.get(b.id) : undefined;
            const given = review ? review.service_rating ?? review.captain_rating ?? review.rating ?? 0 : 0;
            // Each vehicle type once — the "2 vehicles" chip already counts them.
            const vehicles = Array.from(new Set(slab.bookings.map(vehicleLabel))).join(" + ");
            return (
              <div
                key={slab.key}
                role="button"
                tabIndex={0}
                onClick={openDetail}
                onKeyDown={(e) => {
                  if (e.target !== e.currentTarget) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openDetail();
                  }
                }}
                className="flex w-full cursor-pointer items-center gap-4 rounded-2xl border border-[#F3E5B5] bg-white p-4 text-left transition-all hover:shadow-[0_8px_24px_rgba(17,24,39,0.08)] sm:p-5"
              >
                {firstService ? (
                  <img
                    src={serviceImage({ name: firstService }, i)}
                    alt=""
                    loading="lazy"
                    className="hidden h-11 w-11 shrink-0 rounded-xl object-cover sm:block"
                  />
                ) : (
                  <span className="hidden h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black sm:flex">
                    <Sparkles className="h-5 w-5" />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  {/* The SERVICE leads the row — that's what the customer
                      recognises; the booking id is reference data and sits
                      underneath in small mono type. */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <p className="truncate text-sm font-bold text-black">{serviceLabel}</p>
                    {slab.isVisit && (
                      <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                        {slab.vehicleCount} vehicles
                      </span>
                    )}
                    <p className="text-sm text-gray-600">
                      {formatShortDate(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}
                    </p>
                    {isPlanBooking && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                        <Gift className="h-3 w-3" /> Plan
                      </span>
                    )}
                  </div>
                  <div className="mt-1.5 sm:hidden">
                    <StatusBadge status={slab.status} label={customerStatusLabel(slab.status)} />
                  </div>
                  <p className="mt-1 truncate text-xs text-gray-400 sm:mt-0.5">
                    <span className="font-mono-num">
                      {slab.isVisit ? slab.bookings.map((x) => x.booking_number).join(" · ") : b.booking_number}
                    </span>
                    {vehicles ? ` · ${vehicles}` : ""}
                    <span className="font-mono-num text-black md:hidden"> · ₹{Math.round(slab.totalAmount)}</span>
                  </p>
                  {/* Completed bookings carry their rating right on the row:
                      filled stars for what they gave, or a "Rate" button that
                      opens the booking — its page opens the rating window by
                      itself when the booking is completed and unrated. */}
                  {!slab.isVisit && b.status === "completed" && (
                    review ? (
                      <span className="mt-1.5 flex items-center gap-1" title={`You rated ${given}/5`}>
                        {Array.from({ length: 5 }).map((_, si) => (
                          <Star key={si} className={`h-3.5 w-3.5 ${si < given ? "fill-[#E8A900] text-[#E8A900]" : "text-gray-300"}`} />
                        ))}
                        <span className="ml-1 text-xs text-gray-500">You rated {given}/5</span>
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          openDetail();
                        }}
                        className="-ml-1 mt-1 inline-flex items-center gap-1 whitespace-nowrap rounded-full px-1 py-0.5 text-xs text-gray-600 hover:text-black"
                      >
                        {Array.from({ length: 5 }).map((_, si) => (
                          <Star key={si} className="h-3.5 w-3.5 text-gray-300" />
                        ))}
                        <span className="ml-1 font-semibold underline underline-offset-2">Rate this wash</span>
                      </button>
                    )
                  )}
                </div>
                <span className="hidden font-mono-num text-sm font-bold text-black md:block">₹{Math.round(slab.totalAmount)}</span>
                <span className="hidden sm:inline-flex">
                  <StatusBadge status={slab.status} label={customerStatusLabel(slab.status)} />
                </span>
                {canRebook && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/app/book?repeat=${b.id}`);
                    }}
                    title="Book this again"
                    aria-label="Book this again"
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-gray-300 px-2 py-1.5 text-xs font-medium text-black transition-colors hover:border-gray-400 hover:bg-gray-50 sm:px-3"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> <span className="hidden sm:inline">Book again</span>
                  </button>
                )}
                <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
              </div>
            );
          })}
          {more ? (
            loadMore
          ) : (
            loaded.length > PAGE_SIZE && <p className="pt-1 text-center text-xs text-gray-400">That's all {total} bookings.</p>
          )}
        </div>
      )}
    </div>
  );
}
