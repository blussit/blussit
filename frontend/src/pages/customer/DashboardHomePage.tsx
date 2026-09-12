/**
 * Customer dashboard — landing-theme "10 seconds to your car's status":
 * greeting band with the primary CTA, the NEXT booking front and centre,
 * stat tiles, recent bookings, and quick links. All data comes from the
 * same two queries the old page used — this is a presentation upgrade.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, CalendarPlus, Car, Clock, Gift, LifeBuoy, ListChecks, MapPin } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { subscriptionApi } from "../../api/engagement";
import { Badge, Card, CardBody, CardHeader, EmptyState, PageLoader, StatCard, StatusBadge } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";
import { toSlabs } from "../../lib/bookingGroups";

const QUICK_LINKS = [
  { label: "My vehicles", sub: "Add or manage your cars & bikes", to: "/app/vehicles", icon: Car },
  { label: "My addresses", sub: "Pin the doorsteps we come to", to: "/app/addresses", icon: MapPin },
  { label: "Support", sub: "Raise an issue about a booking", to: "/app/support", icon: LifeBuoy },
];

export default function CustomerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const { data: bookings, isLoading: bookingsLoading } = useQuery({
    queryKey: ["my-bookings", "recent"],
    queryFn: () => bookingApi.myBookings({ page: 1, page_size: 5 }),
  });

  const { data: subscriptions } = useQuery({
    queryKey: ["my-subscriptions"],
    queryFn: subscriptionApi.mySubscriptions,
  });

  const activeSubs = (subscriptions || []).filter((s) => s.status === "active");
  // Cars on one visit read as ONE booking everywhere they're listed.
  const slabs = toSlabs(bookings?.data || []);
  const upcoming = slabs.filter((slab) => !["completed", "cancelled"].includes(slab.status));
  const nextSlab = upcoming[0];
  const next = nextSlab?.primary;
  // A booking whose online payment was never finished isn't confirmed and
  // will lose its slot — the one thing on this page worth interrupting for.
  const unpaid = upcoming.find((slab) => slab.status === "awaiting_payment")?.primary;

  return (
    <div className="customer-dashboard space-y-5 bg-white [&_a]:cursor-pointer [&_button]:cursor-pointer">
      <style>{`
        /* Other dashboard cards use the same interaction language. */
        .customer-dashboard [data-dashboard-card],
        .customer-dashboard .dashboard-interactive-card {
          border-color: #E5E7EB !important;
          transition: border-color 160ms ease, box-shadow 160ms ease;
        }

        .customer-dashboard [data-dashboard-card]:hover,
        .customer-dashboard .dashboard-interactive-card:hover {
          border-color: #E8A900 !important;
        }

        /* Inputs/selects/textareas: grey normally, yellow on hover/focus. */
        .customer-dashboard :is(
          input[type="text"],
          input[type="email"],
          input[type="password"],
          input[type="number"],
          input[type="tel"],
          input[type="url"],
          input[type="search"],
          input[type="date"],
          input[type="time"],
          input[type="datetime-local"],
          input:not([type]),
          select,
          textarea
        ) {
          border-color: #E5E7EB !important;
          outline: none;
          transition: border-color 160ms ease, box-shadow 160ms ease;
        }

        .customer-dashboard :is(
          input[type="text"],
          input[type="email"],
          input[type="password"],
          input[type="number"],
          input[type="tel"],
          input[type="url"],
          input[type="search"],
          input[type="date"],
          input[type="time"],
          input[type="datetime-local"],
          input:not([type]),
          select,
          textarea
        ):hover,
        .customer-dashboard :is(
          input[type="text"],
          input[type="email"],
          input[type="password"],
          input[type="number"],
          input[type="tel"],
          input[type="url"],
          input[type="search"],
          input[type="date"],
          input[type="time"],
          input[type="datetime-local"],
          input:not([type]),
          select,
          textarea
        ):focus {
          border-color: #E8A900 !important;
          outline: none;
          box-shadow: 0 0 0 1px rgba(232, 169, 0, 0.08) !important;
        }
      `}</style>
      {/* Plain page header — the black greeting band was dropped (founder
          call): the console panels below carry the page, and the booking
          CTA sits inline where it doesn't compete with them. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-[26px] font-bold leading-tight tracking-[-0.025em] text-black">Welcome back, {user?.full_name?.split(" ")[0]}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {unpaid
              ? `Booking ${unpaid.booking_number} isn't confirmed yet — its payment wasn't completed.`
              : next
                ? `Your next service is on ${format(next.scheduled_date)} · ${next.scheduled_slot}.`
                : "Book a doorstep wash whenever you're ready."}
          </p>
        </div>
        <button
          onClick={() => navigate(unpaid ? `/app/bookings/${unpaid.id}` : "/app/book")}
          className="group inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-[#E8A900] px-5 py-2.5 text-sm font-semibold text-white transition-all hover:bg-[#D99A00] hover:-translate-y-0.5 !shadow-none hover:!shadow-none"
        >
          {unpaid ? (
            <>Finish payment</>
          ) : (
            <>
              <CalendarPlus className="h-4 w-4" /> Book a service
            </>
          )}
          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
        </button>
      </div>

      {/* Stat tiles — same console tile as every other panel, each one a
          drill-down (the link appears on hover, always shown on touch). */}
      <div className="dashboard-stat-grid grid grid-cols-1 gap-3.5 sm:grid-cols-3">
        <StatCard
          className="!border-[#E5E7EB] hover:!border-[#E8A900] transition-colors duration-200"
          label="Upcoming bookings"
          value={upcoming.length}
          hint={next ? `Next on ${format(next.scheduled_date)}` : "Nothing scheduled"}
          icon={ListChecks}
          to="/app/bookings"
        />
        <StatCard
          className="!border-[#E5E7EB] hover:!border-[#E8A900] transition-colors duration-200"
          label="Active plans"
          value={activeSubs.length}
          hint={activeSubs.length ? "Washes included" : "Subscribe and save"}
          icon={Gift}
          to="/app/subscriptions"
          linkLabel={activeSubs.length ? "View all" : "See plans"}
        />
        <StatCard className="!border-[#E5E7EB] hover:!border-[#E8A900] transition-colors duration-200" label="Total bookings" value={bookings?.meta.total ?? 0} hint="All time" icon={Car} to="/app/bookings" />
      </div>

      {/* Subscription above the bookings card on every screen; quick links
          ride in the right column on desktop, at the end on mobile. */}
      <div className="grid grid-cols-1 gap-4.5 lg:grid-cols-3">
        <Card data-dashboard-card className="order-2 lg:col-span-2">
          <CardHeader className="flex items-center justify-between">
            <h2 className="font-semibold text-[var(--color-text-primary)]">{next ? "Your next booking" : "Recent bookings"}</h2>
            <Link to="/app/bookings" className="text-sm font-semibold text-black hover:underline">
              View all
            </Link>
          </CardHeader>
          <CardBody>
            {bookingsLoading ? (
              <PageLoader />
            ) : !bookings?.data.length ? (
              <EmptyState
                title="No bookings yet"
                description="Book your first doorstep service to see it here."
                action={
                  <button onClick={() => navigate("/app/book")} className="inline-flex items-center gap-2 rounded-lg bg-[#111827] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[#1F2937]">
                    Book now <ArrowRight className="h-4 w-4" />
                  </button>
                }
              />
            ) : (
              <div className="space-y-5 bg-white [&_a]:cursor-pointer [&_button]:cursor-pointer">
                {next && (
                  <button
                    onClick={() => navigate(`/app/bookings/${next.id}`)}
                    data-dashboard-card className="flex w-full flex-col gap-3 rounded-xl border border-gray-200 bg-[#FAFAFA] p-4 text-left transition-colors  sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="flex items-center gap-4">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#E8A900] text-white">
                        <Clock className="h-4 w-4" />
                      </span>
                      {/* Service leads, then when — the booking id and the
                          vehicle sit underneath as reference detail. */}
                      <div className="min-w-0">
                        <p className="truncate text-sm font-bold text-black">
                          {nextSlab!.serviceLabel}
                          {nextSlab!.isVisit ? ` · ${nextSlab!.vehicleCount} vehicles` : ""}
                        </p>
                        <p className="text-sm text-gray-600">
                          {format(next.scheduled_date)} · {next.scheduled_slot}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-gray-400">
                          <span className="font-mono-num">{next.booking_number}</span>
                          {nextSlab!.vehicleLabel ? ` · ${nextSlab!.vehicleLabel}` : ""}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <StatusBadge status={nextSlab!.status} />
                      <ArrowRight className="h-4 w-4 text-black" />
                    </div>
                  </button>
                )}
                <div className="divide-y divide-gray-100">
                  {slabs
                    .filter((slab) => slab.key !== nextSlab?.key)
                    .slice(0, 4)
                    .map((slab) => {
                      const b = slab.primary;
                      return (
                      <button key={slab.key} onClick={() => navigate(`/app/bookings/${b.id}`)} className="flex w-full items-center justify-between gap-4 py-3 text-left">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-black">
                            {slab.serviceLabel}
                            {slab.isVisit ? ` · ${slab.vehicleCount} vehicles` : ""}
                          </p>
                          <p className="text-xs text-[var(--color-text-secondary)]">
                            {format(b.scheduled_date)} · {b.scheduled_slot}
                          </p>
                          <p className="mt-0.5 truncate text-xs text-gray-400">
                            <span className="font-mono-num">{b.booking_number}</span>
                            {slab.vehicleLabel ? ` · ${slab.vehicleLabel}` : ""}
                          </p>
                        </div>
                        <div className="flex items-center gap-4">
                          <Badge tone="neutral" className="hidden font-mono-num sm:inline-flex">₹{slab.totalAmount}</Badge>
                          <StatusBadge status={slab.status} />
                        </div>
                      </button>
                      );
                    })}
                </div>
              </div>
            )}
          </CardBody>
        </Card>

        {/* Subscription snapshot — first, everywhere */}
        <Card data-dashboard-card className="order-1 h-fit lg:col-span-2">
            <CardHeader className="flex items-center justify-between">
              <h2 className="font-semibold text-[var(--color-text-primary)]">Subscription</h2>
              <Link to="/app/subscriptions" className="text-sm font-semibold text-black hover:underline">
                Plans
              </Link>
            </CardHeader>
            <CardBody className="!p-6">
              {activeSubs.length ? (
                activeSubs.slice(0, 1).map((s) => (
                  <div key={s.id}>
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold text-[var(--color-text-primary)]">{s.plan_name || "Active plan"}</p>
                      <span className="rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-green-700">Active</span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                      <span className="font-mono-num font-bold text-black">{s.remaining_service_count}</span> of {s.total_service_count} washes left
                    </p>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-gray-100">
                      <div
                        className="h-full rounded-full bg-[#E8A900]"
                        style={{ width: `${s.total_service_count ? Math.round(((s.remaining_service_count ?? 0) / s.total_service_count) * 100) : 0}%` }}
                      />
                    </div>
                    <p className="mt-2 text-xs text-[var(--color-text-secondary)]">Valid until {format(s.end_date)}</p>
                    <button
                      onClick={() => navigate(`/app/book?subscription=${s.id}`)}
                      className="mt-4 inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-900 transition-all hover:border-gray-400 hover:bg-gray-50 hover:-translate-y-0.5"
                    >
                      <Gift className="h-4 w-4 text-[#E8A900]" /> Book with plan
                    </button>
                  </div>
                ))
              ) : (
                <div>
                  <p className="text-sm text-[var(--color-text-secondary)]">No active plan. Subscribe once, save on every wash.</p>
                  <Link to="/app/subscriptions" className="mt-3 inline-flex items-center gap-1.5 text-sm font-bold text-black hover:underline">
                    See plans <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              )}
            </CardBody>
          </Card>

        <div className="order-3 space-y-3 lg:col-start-3 lg:row-start-1 lg:row-span-2">
            {QUICK_LINKS.map((q) => (
              <Link
                key={q.to}
                to={q.to}
                data-dashboard-card className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white shadow-[0_2px_8px_rgba(15,23,42,0.03)] shadow-[0_1px_3px_rgba(15,23,42,0.03)] p-4 transition-all hover:-translate-y-0.5  hover:shadow-[0_10px_24px_rgba(60,40,0,0.07)]"
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
                  <q.icon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-black">{q.label}</p>
                  <p className="truncate text-xs text-gray-400">{q.sub}</p>
                </div>
                <ArrowRight className="ml-auto h-4 w-4 shrink-0 text-gray-300" />
              </Link>
            ))}
        </div>
      </div>
    </div>
  );
}
