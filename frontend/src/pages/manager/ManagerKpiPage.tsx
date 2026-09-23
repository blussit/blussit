import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarClock, Gauge, Gift, IndianRupee, ShoppingBag, Star, UserX } from "lucide-react";
import { analyticsApi, kpiApi } from "../../api/admin";
import { EmptyState, PageLoader, Panel, StatCard } from "../../components/ui";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { RevenueDrillModal } from "../../components/admin/kpi/RevenueDrillModal";
import { formatINR } from "../../components/admin/kpi/charts";
import { paymentApi } from "../../api/payment";
import { useAuth } from "../../context/AuthContext";
import { PERIODS, REVENUE_SCOPES, type RevenueScope } from "../../lib/kpiPeriods";

/**
 * The manager's daily operating picture, in the console shape the founder
 * asked for: ONE headline number, three tiles for the things that need a
 * decision today, and every supporting metric demoted to a quiet strip
 * below — not twelve competing tiles. Each tile drills into the page
 * where the number is actually explained.
 *
 * The Sales section above it is a different lens on the same center —
 * period-selectable (not locked to "today" like the operational tiles
 * below), combined bookings+plans revenue with the exact same drill-down
 * the admin dashboard's hero tile uses, just scoped to this one center.
 */
export default function ManagerKpiPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";

  const [periodKey, setPeriodKey] = useState<string>("today");
  const [revenueScope, setRevenueScope] = useState<RevenueScope>("combined");
  const [revenueDrillOpen, setRevenueDrillOpen] = useState(false);
  const params = useMemo(() => ({ period: periodKey }), [periodKey]);

  const { data, isLoading } = useQuery({
    queryKey: ["manager-kpi", centerId],
    queryFn: () => analyticsApi.managerSummary(centerId),
    enabled: !!centerId,
  });

  const { data: sales, isLoading: salesLoading } = useQuery({
    queryKey: ["manager-kpi-sales", centerId, params],
    queryFn: () => kpiApi.managerOverview(centerId, params),
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
  const cur = sales?.current;
  const periodNoun = periodKey === "today" ? "yesterday" : "previous period";
  const revenueFor = (block: typeof cur) =>
    !block ? 0 : revenueScope === "bookings" ? block.revenue : revenueScope === "plans" ? block.plan_revenue : block.combined_revenue;
  const revenueLabel = revenueScope === "plans" ? "Plan revenue" : revenueScope === "bookings" ? "Booking revenue" : "Total sales";

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
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-black">KPIs</h1>
          <p className="mt-1 text-sm text-gray-500">Sales, compared with {periodNoun}, and today's operations for your center.</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPeriodKey(p.key)}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${periodKey === p.key ? "bg-black text-white" : "border border-[#F3E5B5] bg-white text-gray-600 hover:border-black"}`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-1.5 flex gap-1.5">
          {REVENUE_SCOPES.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => setRevenueScope(s.key)}
              className={`rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${revenueScope === s.key ? "bg-black text-white" : "border border-[#F3E5B5] bg-white text-gray-500 hover:border-black"}`}
            >
              {s.label}
            </button>
          ))}
        </div>
        {salesLoading || !cur ? (
          <div className="h-[104px] animate-pulse rounded-2xl border border-[#F3E5B5] bg-gray-50" />
        ) : (
          <StatCard label={revenueLabel} value={formatINR(revenueFor(cur))} icon={IndianRupee} onClick={() => setRevenueDrillOpen(true)} linkLabel="Details" />
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatCard label="Bookings" value={cur?.bookings ?? 0} hint={`${cur?.completed ?? 0} completed`} icon={ShoppingBag} />
        <StatCard label="Plans sold" value={cur?.plans_sold ?? 0} icon={Gift} />
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

      {/* Who delivered what and collected what — washes (incl. plan
          washes) and cash/online/uncollected, per captain. */}
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

      <RevenueDrillModal
        open={revenueDrillOpen}
        onClose={() => setRevenueDrillOpen(false)}
        params={params}
        defaultTab={revenueScope === "plans" ? "plans" : "bookings"}
        serviceCenterId={centerId}
      />
    </div>
  );
}
