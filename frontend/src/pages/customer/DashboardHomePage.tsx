/**
 * Customer home, top to bottom: a pending payment (if any), the pass on top
 * with a one-tap "Book now", the next visit(s), and the last few visits with
 * "Book again". Two light calls (/subscriptions/my and one small page of
 * /bookings/my); names come from small cached lookups, never the whole
 * catalogue.
 */
import type { ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { CalendarPlus, ChevronRight, Gift, RefreshCw, RotateCcw, UserRound } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { addressApi } from "../../api/profile";
import { subscriptionApi } from "../../api/engagement";
import { Button, Card, StatusBadge } from "../../components/ui";
import { VehicleIcon } from "../../components/shared/VehicleIcon";
import { PassStatusBadge } from "../../components/customer/PassStatusBadge";
import { usePassPurchase } from "../../components/customer/usePassPurchase";
import { useAuth } from "../../context/AuthContext";
import { format, formatDay, formatShortDate, formatSlot, todayIST } from "../../lib/date";
import { toSlabs, visitServiceLabel, visitVehicleLabel, type BookingSlab } from "../../lib/bookingGroups";
import { customerStatusLabel } from "../../lib/customerStatus";
import { buyAgainCandidates, isLivePass, passState } from "../../lib/passState";
import type { ApiPaginated } from "../../lib/api-client";
import type { Service, UserSubscription } from "../../types";

const BOOKINGS_FETCHED = 15;
const UPCOMING_SHOWN = 2;
const RECENT_SHOWN = 3;
/** An ended pass older than this is history, not a "Buy again" prompt. */
const REBUY_WINDOW_MS = 90 * 24 * 60 * 60 * 1000;

const isOpen = (slab: BookingSlab) => !["completed", "cancelled"].includes(slab.status);
const whenKey = (slab: BookingSlab) => `${slab.primary.scheduled_date.slice(0, 10)} ${slab.primary.scheduled_slot}`;

function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-2xl bg-gray-100 ${className}`} />;
}

function TextSkeleton({ className = "w-28" }: { className?: string }) {
  return <span aria-hidden="true" className={`inline-block h-3 animate-pulse rounded bg-gray-100 align-middle ${className}`} />;
}

function SectionHead({ title, to, linkLabel }: { title: string; to?: string; linkLabel?: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-base font-semibold text-black">{title}</h2>
      {to && linkLabel && (
        <Link to={to} className="inline-flex items-center gap-0.5 text-sm font-medium text-gray-600 hover:text-black">
          {linkLabel} <ChevronRight className="h-4 w-4" />
        </Link>
      )}
    </div>
  );
}

/** Month over day, like a desk calendar — the visit's date at a glance. */
function DateTile({ date }: { date: string }) {
  const d = new Date(`${date.slice(0, 10)}T12:00:00+05:30`);
  // en-US: three letters ("SEP"), which fits the tile — en-IN says "Sept".
  const month = d.toLocaleDateString("en-US", { month: "short", timeZone: "Asia/Kolkata" });
  const day = d.toLocaleDateString("en-IN", { day: "numeric", timeZone: "Asia/Kolkata" });
  return (
    <span className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-xl bg-gray-100 text-black">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">{month}</span>
      <span className="font-mono-num text-xl font-bold leading-none">{day}</span>
    </span>
  );
}

function renewLine(sub: UserSubscription): string {
  if (passState(sub) === "renewing") return "Your next month starts once the auto-pay charge goes through.";
  if (passState(sub) === "used_up") return `All washes used · ${sub.auto_renew ? "renews" : "valid till"} ${format(sub.end_date)}`;
  return `${sub.auto_renew ? "Renews" : "Valid till"} ${format(sub.end_date)}`;
}

export default function CustomerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const purchase = usePassPurchase();
  const firstName = user?.full_name?.trim().split(" ")[0];

  const bookingsQuery = useQuery({
    queryKey: ["my-bookings", "dashboard"],
    queryFn: () => bookingApi.myBookings({ page: 1, page_size: BOOKINGS_FETCHED }),
  });
  const subsQuery = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });

  // ---- passes ---------------------------------------------------------------
  const subs = subsQuery.data || [];
  const livePasses = subs
    .filter(isLivePass)
    .sort((a, b) => Number(passState(b) === "active") - Number(passState(a) === "active") || new Date(a.end_date).getTime() - new Date(b.end_date).getTime());
  const rebuy = livePasses.length
    ? undefined
    : buyAgainCandidates(subs).find((s) => Date.now() - new Date(s.end_date).getTime() < REBUY_WINDOW_MS);
  const shownPasses = rebuy ? [rebuy] : livePasses;

  // Names for what a pass covers — only the few rows on screen are looked up.
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const needPlans = !!rebuy || livePasses.some((s) => !s.plan_name);
  const plansQuery = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true), enabled: needPlans, staleTime: 5 * 60 * 1000 });
  const serviceIds = Array.from(new Set(shownPasses.map((s) => s.service_id).filter(Boolean))) as string[];
  const serviceQueries = useQueries({
    queries: serviceIds.map((id) => ({
      queryKey: ["service", id],
      queryFn: () => catalogApi.service(id),
      staleTime: 10 * 60 * 1000,
      // Already have the catalogue from a booking this session? Use it.
      initialData: () => queryClient.getQueryData<ApiPaginated<Service>>(["public-services"])?.data.find((s) => s.id === id),
    })),
  });
  const typeName = (id?: string | null) => vehicleTypes?.find((t) => t.id === id)?.name || "";
  const coversOf = (sub: UserSubscription): ReactNode => {
    const q = sub.service_id ? serviceQueries[serviceIds.indexOf(sub.service_id)] : undefined;
    const service: Service | undefined = q?.data;
    if (q?.isPending || !vehicleTypes) return <TextSkeleton />;
    return [typeName(sub.vehicle_type), service?.name].filter(Boolean).join(" · ");
  };
  const planNameOf = (sub: UserSubscription): ReactNode => {
    if (sub.plan_name) return sub.plan_name;
    if (plansQuery.isLoading) return <TextSkeleton className="w-32" />;
    return plansQuery.data?.find((p) => p.id === sub.plan_id)?.name || "Monthly pass";
  };

  // The booking flow's data, fetched on the way to the button — so the plan
  // booking opens ready instead of waiting on the catalogue.
  const warmBooking = () => {
    void queryClient.prefetchQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }), staleTime: 60_000 });
    void queryClient.prefetchQuery({ queryKey: ["addresses"], queryFn: addressApi.list, staleTime: 60_000 });
  };
  const bookWithPass = (sub: UserSubscription) => navigate(`/app/book?subscription=${sub.id}`);

  // ---- bookings -------------------------------------------------------------
  // A multi-car visit is one card (toSlabs). Soonest visit first, then the latest finished ones.
  const slabs = toSlabs(bookingsQuery.data?.data || []);
  const open = slabs.filter(isOpen).sort((a, b) => whenKey(a).localeCompare(whenKey(b)));
  const unpaid = open.find((s) => s.status === "awaiting_payment");
  const upcoming = open.filter((s) => s !== unpaid).slice(0, UPCOMING_SHOWN);
  // Past visits only — a cancelled booking for a day still ahead isn't one.
  const today = todayIST();
  const recent = slabs
    .filter((s) => s.status === "completed" || (s.status === "cancelled" && s.primary.scheduled_date.slice(0, 10) < today))
    .sort((a, b) => whenKey(b).localeCompare(whenKey(a)))
    .slice(0, RECENT_SHOWN);
  const noBookings = !bookingsQuery.isLoading && !bookingsQuery.isError && slabs.length === 0;

  // ---- render pieces ----------------------------------------------------------
  const passCard = (sub: UserSubscription) => {
    const left = sub.remaining_service_count ?? 0;
    const total = sub.total_service_count || 0;
    const pct = total ? Math.round((left / total) * 100) : 0;
    const active = passState(sub) === "active";
    return (
      <Card key={sub.id} className="p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
              <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate font-display text-lg font-bold leading-tight text-black">{planNameOf(sub)}</p>
              <p className="mt-0.5 truncate text-sm text-gray-500">{coversOf(sub)}</p>
            </div>
          </div>
          <PassStatusBadge sub={sub} />
        </div>

        {active ? (
          <>
            <div className="mt-5 flex items-end justify-between gap-3">
              <p className="text-sm text-gray-600">
                <span className="font-mono-num text-4xl font-bold leading-none text-black">{left}</span>
                <span className="ml-1.5">of {total} washes left</span>
              </p>
            </div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100">
              <div className="h-full rounded-full bg-[#E8A900]" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-2.5 flex items-center gap-1.5 text-xs text-gray-500">
              {sub.auto_renew && <RefreshCw className="h-3.5 w-3.5" />}
              {renewLine(sub)}
            </p>
            <Button
              variant="info"
              size="lg"
              className="mt-5 w-full font-semibold"
              onPointerEnter={warmBooking}
              onFocus={warmBooking}
              onClick={() => bookWithPass(sub)}
            >
              Book now
            </Button>
          </>
        ) : (
          <p className="mt-5 flex items-center gap-1.5 rounded-xl bg-[#FAFAFA] px-3.5 py-3 text-sm text-gray-600">
            {sub.auto_renew && <RefreshCw className="h-4 w-4 shrink-0" />}
            {renewLine(sub)}
          </p>
        )}
      </Card>
    );
  };

  /** Two or more passes: one compact row each, "Book" right on it. */
  const compactPassCard = (sub: UserSubscription) => {
    const left = sub.remaining_service_count ?? 0;
    const total = sub.total_service_count || 0;
    const pct = total ? Math.round((left / total) * 100) : 0;
    const active = passState(sub) === "active";
    return (
      <Card key={sub.id} className="p-4">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
            <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-black">{planNameOf(sub)}</p>
            <p className="truncate text-xs text-gray-500">{coversOf(sub)}</p>
          </div>
          {active ? (
            <Button variant="info" size="sm" className="shrink-0 px-4" onPointerEnter={warmBooking} onFocus={warmBooking} onClick={() => bookWithPass(sub)}>
              Book now
            </Button>
          ) : (
            <PassStatusBadge sub={sub} />
          )}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
            <div className="h-full rounded-full bg-[#E8A900]" style={{ width: `${pct}%` }} />
          </div>
          <span className="shrink-0 text-xs text-gray-600">
            <span className="font-mono-num font-semibold text-black">{left}</span> of {total} left
          </span>
        </div>
        <p className="mt-1.5 text-xs text-gray-500">{renewLine(sub)}</p>
      </Card>
    );
  };

  const rebuyCard = (sub: UserSubscription) => {
    const plan = plansQuery.data?.find((p) => p.id === sub.plan_id);
    return (
      <Card className="flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:p-5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
            <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-black">{planNameOf(sub)}</p>
            <p className="truncate text-xs text-gray-500">{coversOf(sub)}</p>
          </div>
          <PassStatusBadge sub={sub} />
        </div>
        {plan ? (
          <Button
            variant="info"
            className="w-full shrink-0 font-semibold sm:w-auto"
            onClick={() => purchase.start(plan, { vehicleType: sub.vehicle_type, serviceId: sub.service_id, autoPay: true })}
          >
            <RotateCcw className="h-4 w-4" /> Buy again
          </Button>
        ) : plansQuery.isLoading ? null : (
          <Link to="/app/subscriptions" className="shrink-0 text-sm font-semibold text-black hover:underline">
            See passes
          </Link>
        )}
      </Card>
    );
  };

  const upcomingCard = (slab: BookingSlab) => {
    const b = slab.primary;
    const captain = b.captain_id && b.captain_profile?.full_name ? b.captain_profile.full_name : "";
    const planCovered = !!b.subscription_id || b.payment_method === "subscription";
    return (
      <Link
        key={slab.key}
        to={`/app/bookings/${b.id}`}
        className="flex items-start gap-3.5 rounded-2xl border border-[#F3E5B5] bg-white p-4 transition-shadow hover:shadow-[0_8px_24px_rgba(17,24,39,0.08)]"
      >
        <DateTile date={b.scheduled_date} />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-black">{formatDay(b.scheduled_date)}</p>
              <p className="truncate text-sm text-gray-600">{formatSlot(b.scheduled_slot)}</p>
            </div>
            <StatusBadge status={slab.status} label={customerStatusLabel(slab.status)} />
          </div>
          <p className="mt-2 flex items-center gap-1.5 text-sm text-gray-700">
            <VehicleIcon vehicleTypeId={b.vehicle_type || b.vehicle_snapshot?.vehicle_type} className="h-4 w-4 shrink-0 text-gray-400" />
            <span className="truncate">
              {visitServiceLabel(slab)} · {visitVehicleLabel(slab)}
            </span>
          </p>
          {(captain || planCovered) && (
            <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
              {captain && (
                <span className="inline-flex items-center gap-1">
                  <UserRound className="h-3.5 w-3.5" /> {captain}
                </span>
              )}
              {planCovered && (
                <span className="inline-flex items-center gap-1">
                  <Gift className="h-3.5 w-3.5" /> On your pass
                </span>
              )}
            </p>
          )}
        </div>
      </Link>
    );
  };

  const recentRow = (slab: BookingSlab, last: boolean) => {
    const b = slab.primary;
    const cancelled = slab.status === "cancelled";
    return (
      <div key={slab.key} className={`flex items-center gap-3 px-4 py-3.5 ${last ? "" : "border-b border-[#F3E5B5]"}`}>
        <Link to={`/app/bookings/${b.id}`} className="flex min-w-0 flex-1 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
            <VehicleIcon vehicleTypeId={b.vehicle_type || b.vehicle_snapshot?.vehicle_type} className="h-5 w-5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-semibold text-black">{visitServiceLabel(slab)}</span>
            <span className="block truncate text-xs text-gray-500">
              {formatShortDate(b.scheduled_date)} · {visitVehicleLabel(slab)}
              {cancelled ? " · Cancelled" : ""}
            </span>
          </span>
        </Link>
        <Button variant="outline" size="sm" className="shrink-0" onClick={() => navigate(`/app/book?repeat=${b.id}`)}>
          <RotateCcw className="h-3.5 w-3.5" /> Book again
        </Button>
      </div>
    );
  };

  const passSection = subsQuery.isLoading ? (
    <section aria-busy="true">
      <SectionHead title="Your pass" />
      <Skeleton className="h-[248px]" />
    </section>
  ) : subsQuery.isError ? (
    <p className="text-sm text-gray-500">
      Couldn't load your passes.{" "}
      <button type="button" onClick={() => void subsQuery.refetch()} className="font-semibold text-black underline underline-offset-2">
        Try again
      </button>
    </p>
  ) : livePasses.length ? (
    <section>
      <SectionHead title={livePasses.length > 1 ? "Your passes" : "Your pass"} to="/app/subscriptions" linkLabel="Manage" />
      <div className="space-y-3">{livePasses.length === 1 ? passCard(livePasses[0]) : livePasses.map(compactPassCard)}</div>
    </section>
  ) : rebuy ? (
    <section>
      <SectionHead title="Your pass" to="/app/subscriptions" linkLabel="All passes" />
      {rebuyCard(rebuy)}
    </section>
  ) : (
    <Link
      to="/app/subscriptions"
      className="flex items-center gap-3 rounded-2xl border border-[#F3E5B5] bg-white px-4 py-3.5 transition-shadow hover:shadow-[0_8px_24px_rgba(17,24,39,0.08)]"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
        <Gift className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-semibold text-black">Save with a monthly pass</span>
        <span className="block text-xs text-gray-500">Monthly washes, booked in two taps.</span>
      </span>
      <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
    </Link>
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="min-w-0 truncate font-display text-2xl font-bold text-black">Hi {firstName || "there"}</h1>
        <Button variant="info" className="shrink-0 font-semibold" onPointerEnter={warmBooking} onClick={() => navigate("/app/book")}>
          <CalendarPlus className="h-4 w-4" /> Book a wash
        </Button>
      </div>

      {purchase.note && (
        <Card className="flex items-start justify-between gap-3 p-4">
          <p className="text-sm text-gray-700">{purchase.note}</p>
          {!purchase.isPaying && (
            <Button size="sm" variant="ghost" onClick={purchase.clearNote}>
              Dismiss
            </Button>
          )}
        </Card>
      )}

      {unpaid && (
        <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-black">Payment pending</p>
            <p className="mt-0.5 truncate text-sm text-gray-600">
              {visitServiceLabel(unpaid)} · {formatDay(unpaid.primary.scheduled_date)} · {formatSlot(unpaid.primary.scheduled_slot)}
            </p>
          </div>
          <Button variant="info" className="shrink-0" onClick={() => navigate(`/app/bookings/${unpaid.primary.id}`)}>
            Pay ₹{Math.round(unpaid.totalAmount)} now
          </Button>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          {passSection}

          {bookingsQuery.isLoading ? (
            <section aria-busy="true">
              <SectionHead title="Upcoming" />
              <Skeleton className="h-[96px]" />
            </section>
          ) : upcoming.length > 0 ? (
            <section>
              <SectionHead title="Upcoming" to="/app/bookings" linkLabel="All bookings" />
              <div className="space-y-2.5">{upcoming.map(upcomingCard)}</div>
            </section>
          ) : null}
        </div>

        <div className="lg:col-span-2">
          {bookingsQuery.isLoading ? (
            <section aria-busy="true">
              <SectionHead title="Recent" />
              <Skeleton className="h-[204px]" />
            </section>
          ) : bookingsQuery.isError ? (
            <p className="text-sm text-gray-500">
              Couldn't load your bookings.{" "}
              <button type="button" onClick={() => void bookingsQuery.refetch()} className="font-semibold text-black underline underline-offset-2">
                Try again
              </button>
            </p>
          ) : recent.length > 0 ? (
            <section>
              <SectionHead title="Recent" to="/app/bookings" linkLabel="View all" />
              <div className="overflow-hidden rounded-2xl border border-[#F3E5B5] bg-white">
                {recent.map((slab, i) => recentRow(slab, i === recent.length - 1))}
              </div>
            </section>
          ) : noBookings ? (
            <section>
              <SectionHead title="Your bookings" />
              <div className="rounded-2xl border border-dashed border-[#F3E5B5] px-5 py-8 text-center">
                <p className="text-sm font-semibold text-black">No bookings yet</p>
                <p className="mt-1 text-sm text-gray-500">Your first wash is two steps away.</p>
                <Button variant="info" size="sm" className="mt-4" onClick={() => navigate("/app/book")}>
                  <CalendarPlus className="h-4 w-4" /> Book a wash
                </Button>
              </div>
            </section>
          ) : null}
        </div>
      </div>

      {purchase.sheet}
    </div>
  );
}
