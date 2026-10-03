import { useMemo } from "react";
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CalendarPlus, Gift, Sparkles, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { vehicleTypeApi } from "../../api/catalog";
import { visitCarDetail, visitServiceTitle, visitTypeLabel } from "../../components/customer/cars";
import { reviewApi } from "../../api/engagement";
import { btn, card, PageHeader, Segmented, Skeleton, StatusChip } from "../../components/customer/ui";
import { serviceImage } from "../../components/public/landing/shared";
import { formatDay, formatShortDate, formatSlot } from "../../lib/date";
import { toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import type { Booking } from "../../types";

const PAGE_SIZE = 20;
const FINISHED = ["completed", "cancelled"];
type Tab = "upcoming" | "past";

const whenKey = (slab: BookingSlab) => `${slab.primary.scheduled_date.slice(0, 10)} ${slab.primary.scheduled_slot}`;

/**
 * The customer's bookings in two tabs. Upcoming: every open visit, soonest
 * first, grouped by day ("Today", "Tomorrow", …). Past: finished and
 * cancelled ones, newest first, with "Book again".
 *
 * Paged from the server 20 at a time (newest booked first) with "Load more";
 * a multi-car visit is one card (toSlabs). Bookings open at most 7 days
 * ahead, so upcoming ones are always among the newest — "Load more" only
 * reaches further back into the past.
 */
export default function MyBookingsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "past" ? "past" : "upcoming";
  const setTab = (next: Tab) => {
    const p = new URLSearchParams(params);
    if (next === "past") p.set("tab", "past");
    else p.delete("tab");
    setParams(p, { replace: true });
  };

  const query = useInfiniteQuery({
    queryKey: ["my-bookings", "list", ""],
    queryFn: ({ pageParam }) => bookingApi.myBookings({ page: pageParam, page_size: PAGE_SIZE }),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.meta.page < last.meta.total_pages ? last.meta.page + 1 : undefined),
  });
  const total = query.data?.pages[0]?.meta.total ?? 0;
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

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

  const slabs = useMemo(() => toSlabs(loaded), [loaded]);
  const upcoming = useMemo(() => slabs.filter((s) => !FINISHED.includes(s.status)).sort((a, b) => whenKey(a).localeCompare(whenKey(b))), [slabs]);
  const past = useMemo(() => slabs.filter((s) => FINISHED.includes(s.status)).sort((a, b) => whenKey(b).localeCompare(whenKey(a))), [slabs]);

  // Upcoming, one group per day.
  const days = useMemo(() => {
    const groups: { day: string; slabs: BookingSlab[] }[] = [];
    for (const slab of upcoming) {
      const day = slab.primary.scheduled_date.slice(0, 10);
      const last = groups[groups.length - 1];
      if (last && last.day === day) last.slabs.push(slab);
      else groups.push({ day, slabs: [slab] });
    }
    return groups;
  }, [upcoming]);

  // Ratings for exactly the completed bookings on screen.
  const completedIds = useMemo(() => loaded.filter((b) => b.status === "completed").map((b) => b.id), [loaded]);
  const { data: myReviews } = useQuery({
    queryKey: ["my-reviews", "for", completedIds.join(",")],
    queryFn: () => reviewApi.mine(completedIds),
    enabled: tab === "past" && completedIds.length > 0,
    placeholderData: keepPreviousData,
  });
  const reviewByBookingId = new Map((myReviews || []).map((r) => [r.booking_id, r]));

  const upcomingCard = (slab: BookingSlab) => {
    const b = slab.primary;
    const unpaid = slab.status === "awaiting_payment";
    const planCovered = !!b.subscription_id || b.payment_method === "subscription";
    // The service on its own line, then the car type (+ make/plate) — both stay readable at 390px.
    const detail = [visitTypeLabel(slab, vehicleTypes), visitCarDetail(slab, vehicleTypes)].filter(Boolean).join(" · ");
    return (
      <div key={slab.key} className={`${card} flex items-start gap-3 p-4`}>
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]">
          <CalendarPlus className="h-5 w-5" />
        </span>
        <Link to={`/app/bookings/${b.id}`} className="min-w-0 flex-1">
          <p className="truncate tabular-nums text-[15px] font-bold text-[#0E1A33]">{formatSlot(b.scheduled_slot)}</p>
          <p className="mt-0.5 truncate text-sm font-medium text-[#0E1A33]">{visitServiceTitle(slab)}</p>
          {(detail || planCovered) && (
            <p className="truncate text-[13px] text-[#5F6878]">
              {detail}
              {planCovered && (
                <span className={`${detail ? "ml-1.5 " : ""}inline-flex items-center gap-0.5 align-[-2px] text-[#0A66F0]`}>
                  <Gift className="h-3.5 w-3.5" /> On Your Plan
                </span>
              )}
            </p>
          )}
          <StatusChip status={slab.status} className="mt-2" />
        </Link>
        <Link to={`/app/bookings/${b.id}`} className={btn(unpaid ? "primary" : "outline", "sm", "rounded-full")}>
          {unpaid ? `Pay ₹${Math.round(slab.totalAmount)}` : "Details"}
        </Link>
      </div>
    );
  };

  const pastCard = (slab: BookingSlab, i: number) => {
    const b = slab.primary;
    const firstService = (b.combo_name || b.service_names?.[0] || "").replace(/\s*×\d+$/, "");
    const review = !slab.isVisit && b.status === "completed" ? reviewByBookingId.get(b.id) : undefined;
    const given = review ? review.service_rating ?? review.captain_rating ?? review.rating ?? 0 : 0;
    return (
      <div key={slab.key} className={`${card} flex items-center gap-3 p-4`}>
        <Link to={`/app/bookings/${b.id}`} className="flex min-w-0 flex-1 items-center gap-3">
          {firstService ? (
            <img src={serviceImage({ name: firstService }, i)} alt="" loading="lazy" className="h-12 w-12 shrink-0 rounded-[14px] object-cover" />
          ) : (
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px] bg-[#EEF3FA] text-[#0E1A33]">
              <Sparkles className="h-5 w-5" />
            </span>
          )}
          <span className="min-w-0">
            <span className="block truncate text-[13px] font-semibold text-[#5F6878]">
              {formatShortDate(b.scheduled_date)} · {visitTypeLabel(slab, vehicleTypes)}
            </span>
            <span className="block truncate text-[15px] font-semibold text-[#0E1A33]">{visitServiceTitle(slab)}</span>
            <span className="mt-1.5 flex flex-wrap items-center gap-2">
              <StatusChip status={slab.status} />
              {review ? (
                <span className="inline-flex items-center gap-0.5" title={`You rated ${given}/5`}>
                  {Array.from({ length: 5 }).map((_, si) => (
                    <Star key={si} className={`h-3.5 w-3.5 ${si < given ? "fill-[#FFB800] text-[#FFB800]" : "text-[#D5DCE6]"}`} />
                  ))}
                </span>
              ) : !slab.isVisit && b.status === "completed" ? (
                <span className="text-xs font-semibold text-[#0A66F0] underline underline-offset-2">Rate</span>
              ) : null}
            </span>
          </span>
        </Link>
        <button type="button" onClick={() => navigate(`/app/book?repeat=${b.id}`)} className={btn("primary", "sm", "rounded-full")}>
          Book Again
        </button>
      </div>
    );
  };

  const list = tab === "upcoming" ? upcoming : past;
  const more = !!query.hasNextPage;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader
        title="Bookings"
        right={
          <Link to="/app/book" className={btn("soft", "sm")}>
            <CalendarPlus className="h-4 w-4" /> Book A Wash
          </Link>
        }
      />

      <Segmented
        value={tab}
        onChange={setTab}
        options={[
          { value: "upcoming", label: upcoming.length ? `Upcoming · ${upcoming.length}` : "Upcoming" },
          { value: "past", label: "Past" },
        ]}
      />

      {query.isLoading ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-[118px]" />
          <Skeleton className="h-[118px]" />
        </div>
      ) : query.isError ? (
        <div className={`${card} p-6 text-center`}>
          <p className="text-sm text-[#5F6878]">Couldn't load your bookings.</p>
          <button type="button" className={btn("outline", "sm", "mt-3")} onClick={() => void query.refetch()}>
            Try Again
          </button>
        </div>
      ) : !list.length && !(tab === "past" && more) ? (
        <div className="rounded-2xl border border-dashed border-[#CFDCF0] bg-[#F7FAFF] px-6 py-10 text-center">
          <p className="font-display text-base font-bold text-[#0E1A33]">{tab === "upcoming" ? "No Upcoming Washes" : "No Past Bookings Yet"}</p>
          <p className="mt-1 text-sm text-[#5F6878]">{tab === "upcoming" ? "Book one in two quick steps." : "Your finished washes show up here."}</p>
          <Link to="/app/book" className={btn("primary", "md", "mt-4")}>
            Book A Wash
          </Link>
        </div>
      ) : tab === "upcoming" ? (
        <div className="space-y-5">
          {days.map((g) => (
            <section key={g.day}>
              <h2 className="mb-2.5 text-sm font-bold text-[#0E1A33]">{formatDay(g.day)}</h2>
              <div className="space-y-3">{g.slabs.map(upcomingCard)}</div>
            </section>
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {past.map(pastCard)}
          {more ? (
            <div className="flex flex-col items-center gap-1.5 pt-1">
              <button type="button" className={btn("outline", "md")} disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
                {query.isFetchingNextPage ? "Loading…" : "Load More"}
              </button>
              <p className="text-xs text-[#8A94A6]">
                Loaded {loaded.length} of {total} bookings
              </p>
            </div>
          ) : (
            loaded.length > PAGE_SIZE && <p className="pt-1 text-center text-xs text-[#8A94A6]">That's all {total} bookings.</p>
          )}
        </div>
      )}
    </div>
  );
}
