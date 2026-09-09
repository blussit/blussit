import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarClock, CheckCircle2, Clock, Gauge, Star, UserX, Users } from "lucide-react";
import { analyticsApi } from "../../api/admin";
import { Card, CardBody, EmptyState, PageLoader } from "../../components/ui";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { paymentApi } from "../../api/payment";
import { useAuth } from "../../context/AuthContext";

/**
 * Section 20 of the BLUSSIT UX update — a manager's simplified daily
 * operational KPI view, computed live from stored booking/review data
 * (never manually entered). Drill-down into individual captains/bookings
 * happens on the existing Captains / Booking queue pages, linked below,
 * rather than duplicating those here.
 */
export default function ManagerKpiPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const { data, isLoading } = useQuery({ queryKey: ["manager-kpi", centerId], queryFn: () => analyticsApi.managerSummary(centerId), enabled: !!centerId });

  if (!centerId)
    return (
      <EmptyState
        icon={Gauge}
        title="No service center linked"
        description="Your manager account isn't linked to a service center yet — ask an admin to assign one, then these KPIs light up."
      />
    );
  if (isLoading || !data) return <PageLoader />;

  // Section 15/20: each card is also a drill-down entry point, not a
  // dead-end number — clicking it opens wherever that figure is actually
  // explained (the booking queue, captains, or reviews).
  const stats = [
    { label: "Bookings today", value: String(data.bookings_today ?? 0), icon: CalendarClock, to: "/manager/bookings" },
    { label: "Completed", value: String(data.completed_today ?? 0), icon: CheckCircle2, tone: "success" as const, to: "/manager/bookings" },
    { label: "Pending", value: String(data.pending_today ?? 0), icon: Clock, tone: "warning" as const, to: "/manager/bookings" },
    { label: "Unassigned", value: String(data.unassigned_today ?? 0), icon: UserX, tone: (data.unassigned_today ?? 0) > 0 ? ("warning" as const) : ("neutral" as const), to: "/manager/bookings" },
    { label: "Delayed", value: String(data.delayed_today ?? 0), icon: AlertTriangle, tone: (data.delayed_today ?? 0) > 0 ? ("error" as const) : ("neutral" as const), to: "/manager/bookings" },
    { label: "Avg captain travel time", value: data.avg_travel_minutes != null ? `${data.avg_travel_minutes} min` : "—", icon: Clock, to: "/manager/bookings" },
    { label: "Avg service time", value: data.avg_service_minutes != null ? `${data.avg_service_minutes} min` : "—", icon: Clock, to: "/manager/bookings" },
    { label: "Avg completion time", value: data.avg_completion_minutes != null ? `${data.avg_completion_minutes} min` : "—", icon: Clock, to: "/manager/bookings" },
    { label: "On-time %", value: data.on_time_pct != null ? `${data.on_time_pct}%` : "—", icon: Gauge, to: "/manager/bookings" },
    { label: "Captain utilization", value: data.captain_utilization_pct != null ? `${data.captain_utilization_pct}%` : "—", icon: Users, to: "/manager/captains" },
    { label: "Customer rating", value: data.avg_rating != null ? `${data.avg_rating} ★` : "—", icon: Star, to: "/manager/reviews" },
    { label: "Capacity used today", value: data.capacity_utilization_pct != null ? `${data.capacity_utilization_pct}%` : "—", icon: Gauge },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">KPIs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Today's operations for your center.{" "}
          <Link to="/manager/bookings" className="text-[var(--color-primary)] hover:underline">
            Booking queue →
          </Link>{" "}
          ·{" "}
          <Link to="/manager/captains" className="text-[var(--color-primary)] hover:underline">
            Captains →
          </Link>
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {stats.map((s) => {
          const card = (
            <Card className={s.to ? "cursor-pointer transition-shadow hover:shadow-[var(--shadow-lifted)]" : undefined}>
              <CardBody className="flex items-center gap-3 p-4">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                  <s.icon className="h-4.5 w-4.5" />
                </span>
                <div className="min-w-0">
                  <p
                    className={`font-mono-num text-xl font-bold ${
                      s.tone === "success"
                        ? "text-[var(--color-success)]"
                        : s.tone === "warning"
                          ? "text-amber-600"
                          : s.tone === "error"
                            ? "text-[var(--color-error)]"
                            : "text-[var(--color-text-primary)]"
                    }`}
                  >
                    {s.value}
                  </p>
                  <p className="truncate text-xs text-[var(--color-text-secondary)]">{s.label}</p>
                </div>
              </CardBody>
            </Card>
          );
          return s.to ? (
            <Link key={s.label} to={s.to}>
              {card}
            </Link>
          ) : (
            <div key={s.label}>{card}</div>
          );
        })}
      </div>

      {(data.unassigned_today ?? 0) > 0 && (
        <div className="flex items-start gap-2 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          {data.unassigned_today} booking{data.unassigned_today === 1 ? "" : "s"} today still {data.unassigned_today === 1 ? "needs" : "need"} a captain —{" "}
          <Link to="/manager/bookings" className="font-medium underline">
            assign now
          </Link>
          .
        </div>
      )}

      {/* Who collected what — the manager's acknowledgment ledger per
          captain (cash vs online vs completed-but-uncollected). */}
      <CollectionsReportCard
        title="Collections by captain"
        entityLabel="Captain"
        queryKey={`center-collections-${centerId}`}
        fetcher={(params) => paymentApi.centerCollections(centerId, params)}
      />
    </div>
  );
}
