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
import { Badge, Card, CardBody, CardHeader, EmptyState, PageLoader, StatusBadge } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";

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
  const upcoming = (bookings?.data || []).filter((b) => !["completed", "cancelled"].includes(b.status));
  const next = upcoming[0];

  return (
    <div className="space-y-6">
      {/* Greeting band — black panel, gold accents, primary CTA */}
      <div className="relative overflow-hidden rounded-2xl bg-[#101010] px-6 py-7 text-white sm:px-8">
        <div
          className="pointer-events-none absolute right-6 top-4 hidden h-[90px] w-[110px] opacity-50 sm:block"
          style={{ backgroundImage: "radial-gradient(#E8A900 1.1px, transparent 1.1px)", backgroundSize: "10px 10px", maskImage: "linear-gradient(to bottom left, black, transparent)" }}
        />
        <div className="relative flex flex-col justify-between gap-5 sm:flex-row sm:items-center">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#E8A900]">Dashboard</p>
            <h1 className="mt-1.5 font-display text-2xl font-bold">Welcome back, {user?.full_name?.split(" ")[0]}</h1>
            <p className="mt-1 text-sm text-white/60">
              {next
                ? `Your next service is on ${format(next.scheduled_date)} · ${next.scheduled_slot}.`
                : "Your car misses you — book a doorstep wash whenever you're ready."}
            </p>
          </div>
          <button
            onClick={() => navigate("/app/book")}
            className="group inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-[#E8A900] px-6 py-3 text-sm font-bold text-white shadow-[0_6px_16px_rgba(232,169,0,0.3)] transition-all hover:-translate-y-0.5 hover:bg-[#D99A00]"
          >
            <CalendarPlus className="h-4 w-4" /> Book a service
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </button>
        </div>
      </div>

      {/* Stat tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[
          { icon: ListChecks, value: upcoming.length, label: "Upcoming bookings" },
          { icon: Gift, value: activeSubs.length, label: "Active subscriptions" },
          { icon: Car, value: bookings?.meta.total ?? 0, label: "Total bookings" },
        ].map((s) => (
          <Card key={s.label}>
            <CardBody className="flex items-center gap-4 !p-5">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gray-100 text-black">
                <s.icon className="h-5 w-5" />
              </span>
              <div>
                <p className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{s.value}</p>
                <p className="text-sm text-[var(--color-text-secondary)]">{s.label}</p>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>

      {/* Subscription above the bookings card on every screen; quick links
          ride in the right column on desktop, at the end on mobile. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="order-2 lg:col-span-2">
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
                  <button onClick={() => navigate("/app/book")} className="inline-flex items-center gap-2 rounded-xl bg-[#E8A900] px-5 py-2.5 text-sm font-bold text-white hover:bg-[#D99A00]">
                    Book now <ArrowRight className="h-4 w-4" />
                  </button>
                }
              />
            ) : (
              <div className="space-y-4">
                {next && (
                  <button
                    onClick={() => navigate(`/app/bookings/${next.id}`)}
                    className="flex w-full flex-col gap-3 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-4 text-left transition-colors hover:border-[#E8A900]/60 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="flex items-center gap-4">
                      <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#E8A900] text-white">
                        <Clock className="h-5 w-5" />
                      </span>
                      <div>
                        <p className="font-mono-num text-sm font-bold text-black">{next.booking_number}</p>
                        <p className="text-sm text-gray-600">
                          {format(next.scheduled_date)} · {next.scheduled_slot}
                          {(next.combo_name || next.service_names?.length) ? ` · ${next.combo_name || next.service_names?.join(", ")}` : ""}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <StatusBadge status={next.status} />
                      <ArrowRight className="h-4 w-4 text-black" />
                    </div>
                  </button>
                )}
                <div className="divide-y divide-gray-100">
                  {bookings.data
                    .filter((b) => b.id !== next?.id)
                    .slice(0, 4)
                    .map((b) => (
                      <button key={b.id} onClick={() => navigate(`/app/bookings/${b.id}`)} className="flex w-full items-center justify-between gap-4 py-3.5 text-left">
                        <div>
                          <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{b.booking_number}</p>
                          <p className="text-xs text-[var(--color-text-secondary)]">
                            {format(b.scheduled_date)} · {b.scheduled_slot}
                          </p>
                        </div>
                        <div className="flex items-center gap-3">
                          <Badge tone="neutral" className="hidden font-mono-num sm:inline-flex">₹{b.total_amount}</Badge>
                          <StatusBadge status={b.status} />
                        </div>
                      </button>
                    ))}
                </div>
              </div>
            )}
          </CardBody>
        </Card>

        {/* Subscription snapshot — first, everywhere */}
        <Card className="order-1 h-fit lg:col-span-2">
            <CardHeader className="flex items-center justify-between">
              <h2 className="font-semibold text-[var(--color-text-primary)]">Subscription</h2>
              <Link to="/app/subscriptions" className="text-sm font-semibold text-black hover:underline">
                Plans
              </Link>
            </CardHeader>
            <CardBody className="!p-5">
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
                      onClick={() => navigate("/app/book?mode=plan")}
                      className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-[#E8A900] py-2.5 text-sm font-bold text-white transition-colors hover:bg-[#D99A00]"
                    >
                      <Gift className="h-4 w-4" /> Book with plan
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
                className="flex items-center gap-4 rounded-2xl border border-[#F3E5B5] bg-white p-4 transition-all hover:-translate-y-0.5 hover:border-[#E8A900]/50 hover:shadow-[0_10px_24px_rgba(60,40,0,0.07)]"
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
                  <q.icon className="h-5 w-5" />
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
