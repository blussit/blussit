/**
 * Customer Home. Top to bottom: greeting + "Book a Wash", a pending payment
 * (if any), the pass on top with one-tap "Book now" (plan-first — or "Buy
 * again" for a recently ended pass), the next visit(s), the customer's cars
 * with "Clean again", quick services from the live catalogue, and the
 * monthly-plan banner for anyone without a live pass.
 *
 * Light by design: one small page of /bookings/my, /subscriptions/my, the
 * garage (saved + washed cars, merged server-side), and the (cached, shared
 * with the booking flow) catalogue.
 */
import type { ReactNode } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Bike, CarFront, ChevronRight, Droplets, Gift, Plus, RefreshCw, RotateCcw, Sparkles, SprayCan, Star, UserRound, Wind, type LucideIcon } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { addressApi, vehicleApi } from "../../api/profile";
import { subscriptionApi } from "../../api/engagement";
import { PassStatusBadge } from "../../components/customer/PassStatusBadge";
import { usePassPurchase } from "../../components/customer/usePassPurchase";
import { btn, card, greetingIST, IconTile, SectionTitle, Skeleton, StatusChip } from "../../components/customer/ui";
import { GARAGE_QUERY_KEY, garageCta, garageNextOrLast, garagePlate, garageRebookPath, garageTitle, garageTypeLine, typeNameOf, useGarageServices, visitTypeServiceLabel } from "../../components/customer/cars";
import { titleCase } from "../../components/public/landing/shared";
import { VehicleIcon, isBikeType } from "../../components/shared/VehicleIcon";
import { useAuth } from "../../context/AuthContext";
import { format, formatDay, formatSlot } from "../../lib/date";
import { toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import { passHeadlinePrice } from "../../lib/passPricing";
import { buyAgainCandidates, isLivePass, isSocietyPass, passState, societyPassPath } from "../../lib/passState";
import type { ApiPaginated } from "../../lib/api-client";
import type { GarageCar } from "../../api/profile";
import type { Service, UserSubscription } from "../../types";

const BOOKINGS_FETCHED = 15;
const UPCOMING_SHOWN = 2;
const CARS_SHOWN = 3;
const QUICK_MAX = 5;
/** An ended pass older than this is history, not a "Buy again" prompt. */
const REBUY_WINDOW_MS = 90 * 24 * 60 * 60 * 1000;

const isOpen = (slab: BookingSlab) => !["completed", "cancelled"].includes(slab.status);
const whenKey = (slab: BookingSlab) => `${slab.primary.scheduled_date.slice(0, 10)} ${slab.primary.scheduled_slot}`;

/** A quick-service tile's icon, picked from the service's own name. */
function serviceIcon(name: string): LucideIcon {
  if (/bike|scooter/i.test(name)) return Bike;
  if (/waterless/i.test(name)) return Droplets;
  if (/deep|interior/i.test(name)) return SprayCan;
  if (/jet|pressure|foam/i.test(name)) return Wind;
  if (/star|premium|polish/i.test(name)) return Star;
  if (/exterior/i.test(name)) return CarFront;
  return Sparkles;
}

/** "Waterless Service" → "Waterless", "Deep Cleaning" → "Deep Clean". */
const shortServiceName = (name: string) => name.replace(/\s*\(.*\)\s*$/, "").replace(/\s+service$/i, "").replace(/cleaning$/i, "Clean").trim();

function TextSkeleton({ className = "w-28" }: { className?: string }) {
  return <span aria-hidden="true" className={`inline-block h-3 animate-pulse rounded bg-[#EEF3FA] align-middle ${className}`} />;
}

/** Month over day, like a desk calendar. */
function DateTile({ date }: { date: string }) {
  const d = new Date(`${date.slice(0, 10)}T12:00:00+05:30`);
  const month = d.toLocaleDateString("en-US", { month: "short", timeZone: "Asia/Kolkata" });
  const day = d.toLocaleDateString("en-IN", { day: "numeric", timeZone: "Asia/Kolkata" });
  return (
    <span className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]">
      <span className="text-[10px] font-bold uppercase tracking-wide">{month}</span>
      <span className="tabular-nums text-xl font-bold leading-none">{day}</span>
    </span>
  );
}

function renewLine(sub: UserSubscription): string {
  // A society pass: premium washes can run out, the daily washes don't.
  if (isSocietyPass(sub)) {
    return passState(sub) === "used_up"
      ? `Premium washes used · daily washes till ${format(sub.end_date)}`
      : `Valid till ${format(sub.end_date)} · renew on your society page`;
  }
  if (passState(sub) === "renewing") return "Your next month starts once the auto-pay charge goes through.";
  if (passState(sub) === "used_up") return `All washes used · ${sub.auto_renew ? "renews" : "valid till"} ${format(sub.end_date)}`;
  return `${sub.auto_renew ? "Renews" : "Valid till"} ${format(sub.end_date)}`;
}

export default function CustomerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const purchase = usePassPurchase();
  const firstName = user?.full_name?.trim().split(/\s+/)[0];

  const bookingsQuery = useQuery({
    queryKey: ["my-bookings", "dashboard"],
    queryFn: () => bookingApi.myBookings({ page: 1, page_size: BOOKINGS_FETCHED }),
  });
  const subsQuery = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const garageQuery = useQuery({ queryKey: GARAGE_QUERY_KEY, queryFn: vehicleApi.garage, staleTime: 30_000 });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  // The same cached catalogue the booking flow uses — opening a quick
  // service lands on a ready booking screen.
  const servicesQuery = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }), staleTime: 60_000 });
  const services = servicesQuery.data?.data || [];

  // ---- passes ---------------------------------------------------------------
  const subs = subsQuery.data || [];
  const livePasses = subs
    .filter(isLivePass)
    .sort((a, b) => Number(passState(b) === "active") - Number(passState(a) === "active") || new Date(a.end_date).getTime() - new Date(b.end_date).getTime());
  const rebuy = livePasses.length
    ? undefined
    : buyAgainCandidates(subs).find((s) => Date.now() - new Date(s.end_date).getTime() < REBUY_WINDOW_MS);
  const shownPasses = rebuy ? [rebuy] : livePasses;

  // Plans: for an unnamed pass, "Buy again", and the "from ₹…/month" banner.
  const needPlans = subsQuery.isSuccess && (!livePasses.length || livePasses.some((s) => !s.plan_name));
  const plansQuery = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true), enabled: needPlans, staleTime: 5 * 60 * 1000 });
  const serviceIds = Array.from(new Set(shownPasses.map((s) => s.service_id).filter(Boolean))) as string[];
  const serviceQueries = useQueries({
    queries: serviceIds.map((id) => ({
      queryKey: ["service", id],
      queryFn: () => catalogApi.service(id),
      staleTime: 10 * 60 * 1000,
      initialData: () => queryClient.getQueryData<ApiPaginated<Service>>(["public-services"])?.data.find((s) => s.id === id),
    })),
  });
  const coversOf = (sub: UserSubscription): ReactNode => {
    const q = sub.service_id ? serviceQueries[serviceIds.indexOf(sub.service_id)] : undefined;
    if (q?.isPending || !vehicleTypes) return <TextSkeleton />;
    return [typeNameOf(vehicleTypes, sub.vehicle_type), titleCase(q?.data?.name)].filter(Boolean).join(" · ");
  };
  const planNameOf = (sub: UserSubscription): ReactNode => {
    if (sub.plan_name) return titleCase(sub.plan_name);
    if (plansQuery.isLoading) return <TextSkeleton className="w-32" />;
    return titleCase(plansQuery.data?.find((p) => p.id === sub.plan_id)?.name) || "Monthly Pass";
  };

  // Booking data on the way to the button, so the flow opens ready.
  const warmBooking = () => {
    void queryClient.prefetchQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }), staleTime: 60_000 });
    void queryClient.prefetchQuery({ queryKey: ["addresses"], queryFn: addressApi.list, staleTime: 60_000 });
  };
  // A society pass books (a day ahead) and renews on its society page.
  const bookWithPass = (sub: UserSubscription) => navigate((isSocietyPass(sub) && societyPassPath(sub)) || `/app/book?subscription=${sub.id}`);
  const coversLine = (sub: UserSubscription): ReactNode =>
    isSocietyPass(sub) ? (
      <>
        <span className="font-semibold text-[#0A66F0]">Society Plan{sub.society_name ? ` · ${sub.society_name}` : ""}</span> · {coversOf(sub)}
      </>
    ) : (
      coversOf(sub)
    );
  // A society pass counts its premium wash by name: "1 of 2 Star Wash left".
  const washesWord = (sub: UserSubscription) => {
    if (!isSocietyPass(sub)) return "washes";
    const q = sub.service_id ? serviceQueries[serviceIds.indexOf(sub.service_id)] : undefined;
    return titleCase(q?.data?.name) || "premium washes";
  };

  // ---- bookings -------------------------------------------------------------
  const bookings = bookingsQuery.data?.data || [];
  const slabs = toSlabs(bookings);
  const open = slabs.filter(isOpen).sort((a, b) => whenKey(a).localeCompare(whenKey(b)));
  const unpaid = open.find((s) => s.status === "awaiting_payment");
  const upcoming = open.filter((s) => s !== unpaid).slice(0, UPCOMING_SHOWN);

  // ---- cars -----------------------------------------------------------------
  // Saved + washed cars, one row per car, most recent first (server-merged).
  const cars = garageQuery.data || [];
  const carServices = useGarageServices(cars.slice(0, CARS_SHOWN), "nextOrLast");

  // ---- quick services ---------------------------------------------------------
  // One tile per product (bike-count variants share a group), offers first.
  const mains: Service[] = [];
  const seenGroups = new Set<string>();
  for (const s of services) {
    if (s.is_addon || s.is_active === false) continue;
    const key = s.variant_group || s.id;
    if (seenGroups.has(key)) continue;
    seenGroups.add(key);
    mains.push(s);
  }
  mains.sort((a, b) => Number(!!b.offer_tag?.trim()) - Number(!!a.offer_tag?.trim()));
  const quick = mains.length > QUICK_MAX ? mains.slice(0, QUICK_MAX - 1) : mains;
  const quickMore = mains.length > QUICK_MAX;

  // ---- plan banner ------------------------------------------------------------
  const monthly = (plansQuery.data || []).filter((p) => (p.billing_cycle || "monthly") === "monthly");
  const fromPrices = monthly.map((p) => passHeadlinePrice(p, services, vehicleTypes)).filter((n): n is number => n != null);
  const fromPrice = fromPrices.length ? Math.min(...fromPrices) : null;

  // ---- render pieces ----------------------------------------------------------
  const passCard = (sub: UserSubscription) => {
    const left = sub.remaining_service_count ?? 0;
    const total = sub.total_service_count || 0;
    const pct = total ? Math.round((left / total) * 100) : 0;
    const active = passState(sub) === "active";
    return (
      <div key={sub.id} className={`${card} p-5`}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]">
              <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="truncate font-display text-[17px] font-bold leading-tight text-[#0E1A33]">{planNameOf(sub)}</p>
              <p className="mt-0.5 truncate text-sm text-[#5F6878]">{coversLine(sub)}</p>
            </div>
          </div>
          <PassStatusBadge sub={sub} />
        </div>
        {active ? (
          <>
            <p className="mt-5 text-sm text-[#5F6878]">
              <span className="tabular-nums text-4xl font-bold leading-none text-[#0E1A33]">{left}</span>
              <span className="ml-1.5">of {total} {washesWord(sub)} left</span>
            </p>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#EEF3FA]">
              <div className="h-full rounded-full bg-[#0A66F0]" style={{ width: `${pct}%` }} />
            </div>
            <p className="mt-2.5 flex items-center gap-1.5 text-xs text-[#5F6878]">
              {sub.auto_renew && <RefreshCw className="h-3.5 w-3.5" />}
              {renewLine(sub)}
            </p>
            <button type="button" className={btn("primary", "lg", "mt-5 w-full")} onPointerEnter={warmBooking} onFocus={warmBooking} onClick={() => bookWithPass(sub)}>
              Book Now <ArrowRight className="h-4 w-4" />
            </button>
          </>
        ) : (
          <>
            <p className="mt-5 flex items-center gap-1.5 rounded-xl bg-[#F5F8FC] px-3.5 py-3 text-sm text-[#5F6878]">
              {sub.auto_renew && <RefreshCw className="h-4 w-4 shrink-0" />}
              {renewLine(sub)}
            </p>
            {isSocietyPass(sub) && societyPassPath(sub) && (
              <button type="button" className={btn("outline", "md", "mt-3 w-full")} onClick={() => bookWithPass(sub)}>
                Open My Society <ArrowRight className="h-4 w-4" />
              </button>
            )}
          </>
        )}
      </div>
    );
  };

  /** Two or more passes: one compact row each, "Book now" right on it. */
  const compactPassCard = (sub: UserSubscription) => {
    const left = sub.remaining_service_count ?? 0;
    const total = sub.total_service_count || 0;
    const pct = total ? Math.round((left / total) * 100) : 0;
    const active = passState(sub) === "active";
    return (
      <div key={sub.id} className={`${card} p-4`}>
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#E8F0FE] text-[#0A66F0]">
            <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-[#0E1A33]">{planNameOf(sub)}</p>
            <p className="truncate text-xs text-[#5F6878]">{coversLine(sub)}</p>
          </div>
          {active || (isSocietyPass(sub) && societyPassPath(sub)) ? (
            <button type="button" className={btn(active ? "primary" : "outline", "sm")} onPointerEnter={warmBooking} onFocus={warmBooking} onClick={() => bookWithPass(sub)}>
              {active ? "Book Now" : "My Society"}
            </button>
          ) : (
            <PassStatusBadge sub={sub} />
          )}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#EEF3FA]">
            <div className="h-full rounded-full bg-[#0A66F0]" style={{ width: `${pct}%` }} />
          </div>
          <span className="shrink-0 text-xs text-[#5F6878]">
            <span className="tabular-nums font-semibold text-[#0E1A33]">{left}</span> of {total} left
          </span>
        </div>
        <p className="mt-1.5 text-xs text-[#5F6878]">{renewLine(sub)}</p>
      </div>
    );
  };

  const rebuyCard = (sub: UserSubscription) => {
    const plan = plansQuery.data?.find((p) => p.id === sub.plan_id);
    return (
      <div className={`${card} flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:p-5`}>
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#5F6878]">
            <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-[#0E1A33]">{planNameOf(sub)}</p>
            <p className="truncate text-xs text-[#5F6878]">{coversOf(sub)}</p>
          </div>
          <PassStatusBadge sub={sub} />
        </div>
        {plan ? (
          <button
            type="button"
            className={btn("primary", "md", "w-full sm:w-auto")}
            onClick={() => purchase.start(plan, { vehicleType: sub.vehicle_type, serviceId: sub.service_id, autoPay: true })}
          >
            <RotateCcw className="h-4 w-4" /> Buy Again
          </button>
        ) : plansQuery.isLoading ? null : (
          <Link to="/app/subscriptions" className="shrink-0 text-sm font-semibold text-[#0A66F0] hover:underline">
            See Plans
          </Link>
        )}
      </div>
    );
  };

  const upcomingCard = (slab: BookingSlab) => {
    const b = slab.primary;
    const captain = b.captain_profile?.full_name || "";
    const planCovered = !!b.subscription_id || b.payment_method === "subscription";
    return (
      <Link key={slab.key} to={`/app/bookings/${b.id}`} className={`${card} flex items-start gap-3.5 p-4 transition-shadow hover:shadow-[0_12px_32px_-16px_rgba(14,26,51,0.28)]`}>
        <DateTile date={b.scheduled_date} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-[#0E1A33]">
            {formatDay(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}
          </p>
          <p className="mt-0.5 truncate text-sm text-[#5F6878]">{visitTypeServiceLabel(slab, vehicleTypes)}</p>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <StatusChip status={slab.status} />
            {captain && (
              <span className="inline-flex items-center gap-1 text-xs text-[#5F6878]">
                <UserRound className="h-3.5 w-3.5" /> {captain}
              </span>
            )}
            {planCovered && (
              <span className="inline-flex items-center gap-1 text-xs text-[#5F6878]">
                <Gift className="h-3.5 w-3.5" /> On Your Plan
              </span>
            )}
          </div>
        </div>
        <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-[#A3ADBD]" />
      </Link>
    );
  };

  /** One car: its name (make, else plate, else type), then its type and
   *  plate, and "Next Wash 3 Oct · Star Wash" — and one tap to book it again. */
  const carRow = (car: GarageCar, last: boolean) => {
    const title = garageTitle(car);
    const plate = garagePlate(car);
    const idLine = [garageTypeLine(car), plate].filter(Boolean).join(" · ");
    const washLine = garageNextOrLast(car, carServices); // one date here; the garage shows both
    return (
      <div key={car.id} className={`flex items-center gap-3.5 px-4 py-3.5 ${last ? "" : "border-b border-[#EEF2F7]"}`}>
        <span className="flex h-12 w-14 shrink-0 items-center justify-center rounded-[14px] bg-[#EEF3FA] text-[#0E1A33]">
          {isBikeType(car.vehicle_type_name) ? <Bike className="h-6 w-6" /> : <CarFront className="h-6 w-6" />}
        </span>
        <div className="min-w-0 flex-1">
          <p className={`truncate text-[15px] font-semibold text-[#0E1A33] ${title === car.registration_number ? "tabular-nums tracking-wide" : ""}`}>{title}</p>
          {idLine && <p className={`truncate text-xs text-[#5F6878] ${plate ? "tabular-nums tracking-wide" : ""}`}>{idLine}</p>}
          {washLine && <p className="line-clamp-2 text-xs leading-snug text-[#5F6878]">{washLine}</p>}
        </div>
        <Link to={garageRebookPath(car)} onPointerEnter={warmBooking} className={btn("primary", "sm", "rounded-full px-4")}>
          {garageCta(car)}
        </Link>
      </div>
    );
  };

  const carsSection = (
    <section>
      <SectionTitle title="Your Cars" to="/app/garage" linkLabel={cars.length ? "Garage" : "Add A Car"} />
      {garageQuery.isLoading ? (
        <Skeleton className="h-[84px]" />
      ) : cars.length ? (
        <div className={`${card} overflow-hidden`}>{cars.slice(0, CARS_SHOWN).map((c, i, arr) => carRow(c, i === arr.length - 1))}</div>
      ) : (
        <Link to="/app/garage?add=1" className="flex items-center gap-3.5 rounded-2xl border border-dashed border-[#CFDCF0] bg-[#F7FAFF] px-4 py-4 transition-colors hover:border-[#0A66F0]">
          <IconTile icon={Plus} />
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-semibold text-[#0E1A33]">Add Your Car</span>
            <span className="block text-xs text-[#5F6878]">Save it once, book it again in one tap.</span>
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-[#A3ADBD]" />
        </Link>
      )}
    </section>
  );

  const quickSection = (
    <section>
      <SectionTitle title="Quick Services" />
      {servicesQuery.isLoading ? (
        <Skeleton className="h-[92px]" />
      ) : (
        <div className="grid gap-2 sm:gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(4, quick.length + (quickMore ? 1 : 0))}, minmax(0, 1fr))` }}>
          {quick.map((s) => {
            const Icon = serviceIcon(s.name);
            const offer = !!s.offer_tag?.trim();
            return (
              <Link key={s.id} to={`/app/book?service=${encodeURIComponent(s.slug || s.id)}`} onPointerEnter={warmBooking} className="group flex min-w-0 flex-col items-center gap-1.5 text-center">
                <span
                  className={`relative flex aspect-square w-full max-w-[68px] items-center justify-center rounded-2xl border transition-colors ${
                    offer ? "border-[#FFE58A] bg-[#FFF8D6] text-[#0E1A33]" : "border-[#E4E9F1] bg-[#F5F8FD] text-[#0A66F0] group-hover:border-[#0A66F0]"
                  }`}
                >
                  <Icon className="h-6 w-6" />
                  {offer && <span className="absolute -top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-[#FFD21F] px-1.5 py-px text-[9px] font-bold uppercase text-[#0E1A33]">Offer</span>}
                </span>
                <span className="w-full truncate text-[11px] font-medium text-[#0E1A33] sm:text-xs">{titleCase(shortServiceName(s.name))}</span>
              </Link>
            );
          })}
          {quickMore && (
            <Link to="/app/book" className="group flex min-w-0 flex-col items-center gap-1.5 text-center">
              <span className="flex aspect-square w-full max-w-[68px] items-center justify-center rounded-2xl border border-[#E4E9F1] bg-white text-[#5F6878] group-hover:border-[#0A66F0]">
                <Plus className="h-6 w-6" />
              </span>
              <span className="text-[11px] font-medium text-[#0E1A33] sm:text-xs">More</span>
            </Link>
          )}
        </div>
      )}
    </section>
  );

  const planBanner = (
    <Link
      to="/app/subscriptions"
      className="relative block overflow-hidden rounded-2xl bg-[linear-gradient(120deg,#0A66F0_0%,#2F7FF5_60%,#5B9BFA_100%)] p-5 text-white shadow-[0_14px_30px_-16px_rgba(10,102,240,0.7)]"
    >
      <span aria-hidden className="pointer-events-none absolute -right-8 -top-10 h-36 w-36 rounded-full bg-white/10" />
      <span aria-hidden className="pointer-events-none absolute -bottom-12 right-16 h-28 w-28 rounded-full bg-white/10" />
      <span className="relative flex flex-wrap items-center justify-between gap-4">
        <span className="min-w-0">
          <span className="block font-display text-[18px] font-bold">Monthly Care Plan</span>
          <span className="mt-0.5 block text-sm text-white/85">
            {fromPrice != null ? (
              <>
                From <span className="tabular-nums font-semibold text-white">₹{fromPrice}</span>/month
              </>
            ) : (
              "Regular washes, booked in two taps."
            )}
          </span>
        </span>
        <span className={btn("yellow", "md", "rounded-full px-5")}>View Plans</span>
      </span>
    </Link>
  );

  const hasLivePass = livePasses.length > 0;
  const passSection = subsQuery.isLoading ? (
    <section aria-busy="true">
      <SectionTitle title="Your Plan" />
      <Skeleton className="h-[236px]" />
    </section>
  ) : subsQuery.isError ? (
    <p className="text-sm text-[#5F6878]">
      Couldn't load your plans.{" "}
      <button type="button" onClick={() => void subsQuery.refetch()} className="font-semibold text-[#0A66F0] underline underline-offset-2">
        Try Again
      </button>
    </p>
  ) : hasLivePass ? (
    <section>
      <SectionTitle title={livePasses.length > 1 ? "Your Plans" : "Your Plan"} to="/app/subscriptions" linkLabel="Manage" />
      <div className="space-y-3">{livePasses.length === 1 ? passCard(livePasses[0]) : livePasses.map(compactPassCard)}</div>
    </section>
  ) : rebuy ? (
    <section>
      <SectionTitle title="Your Plan" to="/app/subscriptions" linkLabel="All Plans" />
      {rebuyCard(rebuy)}
    </section>
  ) : null;

  return (
    <div className="space-y-6 lg:space-y-8">
      <section className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="text-[15px] text-[#5F6878]">{greetingIST()},</p>
          <h1 className="mt-0.5 truncate font-display text-[28px] font-bold leading-tight text-[#0E1A33] lg:text-[32px]">
            {firstName || "there"} <span aria-hidden>👋</span>
          </h1>
          <p className="mt-1 text-[15px] text-[#5F6878]">Your car deserves some care today.</p>
        </div>
        <Link to="/app/book" onPointerEnter={warmBooking} onFocus={warmBooking} className={btn("primary", "lg", "w-full sm:w-auto sm:min-w-[230px]")}>
          <Star className="h-[18px] w-[18px] fill-current" /> Book A Wash <ArrowRight className="h-[18px] w-[18px]" />
        </Link>
      </section>

      {purchase.note && (
        <div className={`${card} flex items-start justify-between gap-3 p-4`}>
          <p className="text-sm text-[#0E1A33]">{purchase.note}</p>
          {!purchase.isPaying && (
            <button type="button" className={btn("ghost", "sm")} onClick={purchase.clearNote}>
              Dismiss
            </button>
          )}
        </div>
      )}

      {unpaid && (
        <div className="flex flex-col gap-3 rounded-2xl border border-[#FFD8A8] bg-[#FFF8EE] p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#0E1A33]">Payment Pending</p>
            <p className="mt-0.5 truncate text-sm text-[#5F6878]">
              {visitTypeServiceLabel(unpaid, vehicleTypes)} · {formatDay(unpaid.primary.scheduled_date)} · {formatSlot(unpaid.primary.scheduled_slot)}
            </p>
          </div>
          <Link to={`/app/bookings/${unpaid.primary.id}`} className={btn("primary", "md")}>
            Pay ₹{Math.round(unpaid.totalAmount)} Now
          </Link>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5 lg:gap-8">
        <div className="space-y-6 lg:col-span-3">
          {passSection}

          {bookingsQuery.isLoading ? (
            <section aria-busy="true">
              <SectionTitle title="Upcoming" />
              <Skeleton className="h-[96px]" />
            </section>
          ) : bookingsQuery.isError ? (
            <p className="text-sm text-[#5F6878]">
              Couldn't load your bookings.{" "}
              <button type="button" onClick={() => void bookingsQuery.refetch()} className="font-semibold text-[#0A66F0] underline underline-offset-2">
                Try Again
              </button>
            </p>
          ) : upcoming.length > 0 ? (
            <section>
              <SectionTitle title="Upcoming" to="/app/bookings" linkLabel="All Bookings" />
              <div className="space-y-2.5">{upcoming.map(upcomingCard)}</div>
            </section>
          ) : null}

          {carsSection}
        </div>

        <div className="space-y-6 lg:col-span-2">
          {quickSection}
          {subsQuery.isSuccess && !hasLivePass && !rebuy && planBanner}
        </div>
      </div>

      {purchase.sheet}
    </div>
  );
}
