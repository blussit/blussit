import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Gauge, IndianRupee, Percent, ShoppingBag, Star, Users, XCircle } from "lucide-react";
import { analyticsApi } from "../../api/admin";
import { Card, CardBody, CardHeader, PageLoader } from "../../components/ui";

export default function AdminDashboardPage() {
  const { data, isLoading } = useQuery({ queryKey: ["admin-dashboard"], queryFn: analyticsApi.dashboard });
  const { data: trends } = useQuery({ queryKey: ["admin-trends"], queryFn: () => analyticsApi.bookingTrends(14) });

  if (isLoading || !data) return <PageLoader />;

  // Section 14/15: the most important operational KPIs first — what's
  // happening right now, not every possible metric at once. Each card is
  // also a drill-down entry point (Section 15) into wherever that number
  // is actually explained, rather than a dead-end tile.
  const operationalStats = [
    { label: "Total bookings", value: String(data.total_bookings), icon: ShoppingBag, to: "/admin/bookings" },
    { label: "Completed", value: String(data.completed_bookings), icon: CheckCircle2, tone: "success" as const, to: "/admin/bookings" },
    { label: "Pending", value: String(data.pending_bookings), icon: Clock, tone: "warning" as const, to: "/admin/bookings" },
    { label: "Cancelled", value: String(data.cancelled_bookings), icon: XCircle, tone: "neutral" as const, to: "/admin/bookings" },
    { label: "Delayed", value: String(data.delayed_bookings), icon: AlertTriangle, tone: (data.delayed_bookings as number) > 0 ? ("error" as const) : ("neutral" as const), to: "/admin/bookings" },
    { label: "Today's capacity used", value: data.capacity_utilization_pct != null ? `${data.capacity_utilization_pct}%` : "—", icon: Gauge, to: "/admin/service-centers" },
    { label: "Avg service time", value: data.avg_service_minutes != null ? `${data.avg_service_minutes} min` : "—", icon: Clock, to: "/admin/bookings" },
    { label: "Avg travel time", value: data.avg_travel_minutes != null ? `${data.avg_travel_minutes} min` : "—", icon: Clock, to: "/admin/bookings" },
    { label: "Avg completion time", value: data.avg_completion_minutes != null ? `${data.avg_completion_minutes} min` : "—", icon: Clock, to: "/admin/bookings" },
    { label: "Avg customer rating", value: data.avg_rating != null ? `${data.avg_rating} ★` : "—", icon: Star, to: "/admin/reviews" },
  ];

  const businessStats = [
    { label: "Total revenue", value: `₹${data.total_revenue}`, icon: IndianRupee },
    { label: "Today's orders", value: String(data.todays_orders), icon: ShoppingBag },
    { label: "Active customers", value: String(data.active_customers), icon: Users },
    { label: "Repeat customer rate", value: `${data.repeat_customer_rate}%`, icon: Percent },
  ];

  const maxRevenue = Math.max(...(trends || []).map((t) => t.revenue), 1);

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Analytics dashboard</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Operational health at a glance. <Link to="/admin/bookings" className="text-[var(--color-primary)] hover:underline">Drill into a service center →</Link>
        </p>
      </div>

      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Operations today</p>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          {operationalStats.map((s) => (
            <StatCard key={s.label} {...s} />
          ))}
        </div>
      </div>

      <div>
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Business</p>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {businessStats.map((s) => (
            <StatCard key={s.label} {...s} />
          ))}
        </div>
      </div>

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Booking trends (last 14 days)</h2>
        </CardHeader>
        <CardBody>
          <div className="flex h-40 items-end gap-2">
            {(trends || []).map((t) => (
              <div key={t.date} className="flex flex-1 flex-col items-center gap-1.5">
                <div
                  className="w-full rounded-t-md bg-[var(--color-primary)] transition-all"
                  style={{ height: `${Math.max((t.revenue / maxRevenue) * 100, 4)}%` }}
                  title={`₹${t.revenue} · ${t.bookings} bookings`}
                />
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Best performing captains</h2>
          </CardHeader>
          <CardBody className="space-y-3">
            {(data.best_performing_captains as { captain_id: string; jobs_completed: number; revenue: number }[]).map((c) => (
              <div key={c.captain_id} className="flex items-center justify-between text-sm">
                <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{c.captain_id.slice(-6)}</span>
                <span>{c.jobs_completed} jobs · ₹{c.revenue}</span>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Best service centers</h2>
          </CardHeader>
          <CardBody className="space-y-3">
            {(data.best_service_centers as { service_center_id: string; jobs_completed: number; revenue: number }[]).map((c) => (
              <div key={c.service_center_id} className="flex items-center justify-between text-sm">
                <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{c.service_center_id.slice(-6)}</span>
                <span>{c.jobs_completed} jobs · ₹{c.revenue}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
  to,
}: {
  label: string;
  value: string;
  icon: typeof IndianRupee;
  tone?: "success" | "warning" | "error" | "neutral";
  /** When set, the whole card is a drill-down link (Section 15) instead of
   * a dead-end number. */
  to?: string;
}) {
  const toneClass = tone === "success" ? "text-[var(--color-success)]" : tone === "warning" ? "text-amber-600" : tone === "error" ? "text-[var(--color-error)]" : "text-[var(--color-text-primary)]";
  const body = (
    <CardBody className="flex items-center gap-3 p-4">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
        <Icon className="h-4.5 w-4.5" />
      </span>
      <div className="min-w-0">
        <p className={`font-mono-num text-xl font-bold ${toneClass}`}>{value}</p>
        <p className="truncate text-xs text-[var(--color-text-secondary)]">{label}</p>
      </div>
    </CardBody>
  );
  if (to) {
    return (
      <Link to={to}>
        <Card className="cursor-pointer transition-shadow hover:shadow-[var(--shadow-lifted)]">{body}</Card>
      </Link>
    );
  }
  return <Card>{body}</Card>;
}
