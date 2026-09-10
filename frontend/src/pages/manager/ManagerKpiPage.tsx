import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarClock, Gauge, Star, UserX } from "lucide-react";
import { analyticsApi } from "../../api/admin";
import { EmptyState, PageLoader, Panel, StatCard } from "../../components/ui";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { paymentApi } from "../../api/payment";
import { useAuth } from "../../context/AuthContext";

/**
 * The manager's daily operating picture, in the console shape the founder
 * asked for: ONE headline number, three tiles for the things that need a
 * decision today, and every supporting metric demoted to a quiet strip
 * below — not twelve competing tiles. Each tile drills into the page
 * where the number is actually explained.
 */
export default function ManagerKpiPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const { data, isLoading } = useQuery({
    queryKey: ["manager-kpi", centerId],
    queryFn: () => analyticsApi.managerSummary(centerId),
    enabled: !!centerId,
  });

  if (!centerId)
    return (
      <EmptyState
        icon={Gauge}
        title="No service center linked"
        description="Your manager account isn't linked to a service center yet — ask an admin to assign one, then these KPIs light up."
      />
    );
  if (isLoading || !data) return <PageLoader />;

  const unassigned = data.unassigned_today ?? 0;
  const delayed = data.delayed_today ?? 0;

  // Supporting metrics — real, but not decisions: one quiet row, not tiles.
  const detail = [
    { label: "Avg travel", value: data.avg_travel_minutes != null ? `${data.avg_travel_minutes} min` : "—" },
    { label: "Avg service", value: data.avg_service_minutes != null ? `${data.avg_service_minutes} min` : "—" },
    { label: "Avg completion", value: data.avg_completion_minutes != null ? `${data.avg_completion_minutes} min` : "—" },
    { label: "On-time", value: data.on_time_pct != null ? `${data.on_time_pct}%` : "—" },
    { label: "Captain utilization", value: data.captain_utilization_pct != null ? `${data.captain_utilization_pct}%` : "—" },
    { label: "Capacity used", value: data.capacity_utilization_pct != null ? `${data.capacity_utilization_pct}%` : "—" },
  ];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-black">KPIs</h1>
        <p className="mt-1 text-sm text-gray-500">Today's operations for your center.</p>
      </div>

      <StatCard
        label="Bookings today"
        value={data.bookings_today ?? 0}
        hint={`${data.completed_today ?? 0} completed · ${data.pending_today ?? 0} pending`}
        icon={CalendarClock}
        to="/manager/bookings"
        linkLabel="Booking queue"
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard
          label="Needs a captain"
          value={unassigned}
          hint={unassigned > 0 ? "Assign before the slot starts" : "Everything is assigned"}
          icon={UserX}
          tone={unassigned > 0 ? "warning" : "muted"}
          to="/manager/bookings"
          linkLabel="Assign"
        />
        <StatCard
          label="Delayed"
          value={delayed}
          hint={delayed > 0 ? "Running past the promised time" : "All on schedule"}
          icon={AlertTriangle}
          tone={delayed > 0 ? "error" : "muted"}
          to="/manager/bookings"
        />
        <StatCard
          label="Customer rating"
          value={data.avg_rating != null ? `${data.avg_rating}★` : "—"}
          hint="Average across reviewed jobs"
          icon={Star}
          to="/manager/reviews"
          linkLabel="Reviews"
        />
      </div>

      <Panel title="Service performance" description="Averages across this center's recent jobs.">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
          {detail.map((d) => (
            <div key={d.label}>
              <dt className="text-xs text-gray-400">{d.label}</dt>
              <dd className="font-mono-num mt-0.5 text-lg font-bold text-black">{d.value}</dd>
            </div>
          ))}
        </dl>
      </Panel>

      {/* Who collected what — the manager's acknowledgment ledger per
          captain (cash vs online vs completed-but-uncollected). */}
      <CollectionsReportCard
        title="Collections by captain"
        entityLabel="Captain"
        queryKey={`center-collections-${centerId}`}
        fetcher={(params) => paymentApi.centerCollections(centerId, params)}
      />

      {unassigned > 0 && (
        <p className="text-sm text-gray-500">
          {unassigned} booking{unassigned === 1 ? "" : "s"} today still {unassigned === 1 ? "needs" : "need"} a captain —{" "}
          <Link to="/manager/bookings" className="font-semibold text-black underline">
            assign now
          </Link>
          .
        </p>
      )}
    </div>
  );
}
