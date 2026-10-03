import { useMemo, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CalendarClock,
  CalendarPlus,
  Car,
  CheckCircle2,
  ClipboardCheck,
  Gift,
  IndianRupee,
  Package,
  ShoppingBag,
  Sparkles,
  Timer,
  UserX,
  Users,
  type LucideIcon,
} from "lucide-react";
import { analyticsApi, kpiApi, type ManagerDashboard } from "../../api/admin";
import { paymentApi } from "../../api/payment";
import { EmptyState } from "../../components/ui";
import { CollectionsReportCard } from "../../components/shared/CollectionsReport";
import { RevenueDrillModal } from "../../components/admin/kpi/RevenueDrillModal";
import { DeltaPill, formatINR } from "../../components/admin/kpi/charts";
import { useAuth } from "../../context/AuthContext";
import { ISSUE_LABELS } from "../../lib/constants";
import { formatDay } from "../../lib/date";
import { carAndService, toTitle } from "../../lib/titleCase";
import { PERIODS, REVENUE_SCOPES, type RevenueScope } from "../../lib/kpiPeriods";

/* v2 staff palette — same values as the shell + booking page. */
const C = {
  navy: "#0E1A33",
  blue: "#0A66F0",
  muted: "#5F6878",
  line: "#E4E9F1",
  tint: "#E8F0FE",
  track: "#EEF3FA",
  green: "#16A34A",
  amber: "#D97706",
  red: "#DC2626",
};

/**
 * The manager's ONE home screen (Dashboard + KPIs merged, 2026-10).
 *
 * Top to bottom, in the order a manager decides things:
 *  1. what needs a decision NOW (needs a captain, late starts, open issues,
 *     complaints, low stock) — quiet when everything is fine;
 *  2. sales for a chosen period, compared with the previous one, with the
 *     same drill-down the admin dashboard uses;
 *  3. what was washed — per service and per car type — and plans sold;
 *  4. today's board: slot load and who is free to take a job;
 *  5. collections per captain and service quality.
 *
 * Every number comes from ONE server aggregation scoped to this manager's
 * center (GET /analytics/manager-dashboard/{center}); nothing is counted
 * from a page of rows in the browser.
 */
export default function ManagerDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const centerId = user?.service_center_id || "";

  const [periodKey, setPeriodKey] = useState<string>("today");
  const [revenueScope, setRevenueScope] = useState<RevenueScope>("combined");
  const [drillOpen, setDrillOpen] = useState(false);
  const [drillTab, setDrillTab] = useState<"bookings" | "plans" | null>(null);
  const params = useMemo(() => ({ period: periodKey }), [periodKey]);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["manager-dashboard", centerId, params],
    queryFn: () => kpiApi.managerDashboard(centerId, params),
    enabled: !!centerId,
    placeholderData: keepPreviousData,
    refetchInterval: 60000,
  });

  // Service quality averages (all-time, center) — the small strip at the end.
  const { data: quality } = useQuery({
    queryKey: ["manager-kpi", centerId],
    queryFn: () => analyticsApi.managerSummary(centerId),
    enabled: !!centerId,
    staleTime: 5 * 60 * 1000,
  });

  if (!centerId) {
    return (
      <EmptyState
        icon={Users}
        title="No Service Center Linked"
        description="Your manager account isn't assigned to a center yet — an admin can link it, then this dashboard fills in."
      />
    );
  }

  const periodLabel = PERIODS.find((p) => p.key === periodKey)?.label || "";
  const prevNoun = periodKey === "today" ? "yesterday" : "the previous period";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold text-[#0E1A33]">Dashboard</h1>
          <p className="mt-1 text-sm text-[#5F6878]">
            {data?.center.name ? `${data.center.name} · ` : ""}
            {formatDay(data?.today.date || "")}
          </p>
        </div>
        <div className="flex w-full flex-wrap gap-2 sm:w-auto">
          <button
            type="button"
            onClick={() => navigate("/manager/new-booking")}
            className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-[12px] bg-[#FFD21F] px-4 text-sm font-bold text-[#0E1A33] shadow-[0_8px_20px_-12px_rgba(232,169,0,0.8)] transition hover:bg-[#FFC800] sm:flex-none"
          >
            <CalendarPlus className="h-4 w-4" /> New Booking
          </button>
          <button
            type="button"
            onClick={() => navigate("/manager/log-job")}
            className="inline-flex h-11 flex-1 items-center justify-center gap-2 rounded-[12px] border border-[#E4E9F1] bg-white px-4 text-sm font-semibold text-[#0E1A33] transition hover:border-[#0A66F0] hover:text-[#0A66F0] sm:flex-none"
          >
            <ClipboardCheck className="h-4 w-4" /> Log A Done Job
          </button>
        </div>
      </div>

      {isError && !data ? (
        <div className="rounded-[14px] border border-[#FAD4D4] bg-[#FFF5F5] p-4 text-sm text-[#B42318]">
          Couldn't load the dashboard.{" "}
          <button type="button" className="font-semibold underline" onClick={() => refetch()}>
            Try Again
          </button>
        </div>
      ) : null}

      {/* 1. Needs a decision now */}
      {isLoading || !data ? <SkeletonRow /> : <AttentionStrip ops={data.ops} today={data.today} />}

      {/* 2. Sales */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-bold text-[#0E1A33]">Sales</h2>
          <ChipGroup
            options={PERIODS.map((p) => ({ key: p.key, label: p.label }))}
            value={periodKey}
            onChange={setPeriodKey}
            busy={isFetching && !isLoading}
          />
        </div>
        {isLoading || !data ? (
          <SkeletonRow tall />
        ) : (
          <SalesBlock
            data={data}
            scope={revenueScope}
            onScope={setRevenueScope}
            prevNoun={prevNoun}
            onDrill={(tab) => {
              setDrillTab(tab);
              setDrillOpen(true);
            }}
          />
        )}
      </section>

      {/* 3. What was washed */}
      {data && (
        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Block
            title="Washes By Service"
            subtitle={`Completed · ${periodLabel.toLowerCase()}`}
            icon={Sparkles}
            right={<span className="font-mono-num text-sm font-semibold text-[#0E1A33]">{data.washes.washes} done</span>}
          >
            <BarList
              rows={data.services.map((s) => ({
                key: s.service_id,
                label: toTitle(s.name),
                tag: s.is_addon ? "Add-On" : undefined,
                value: s.washes,
                money: s.revenue,
              }))}
              empty={`No washes completed ${periodKey === "today" ? "yet today" : "in this period"}.`}
              color={C.blue}
            />
          </Block>
          <Block title="Washes By Car Type" subtitle={`Completed · ${periodLabel.toLowerCase()}`} icon={Car}>
            <BarList
              rows={data.vehicle_types.map((t) => ({ key: t.vehicle_type_id || t.name, label: toTitle(t.name), value: t.washes, money: t.revenue }))}
              empty="Nothing to break down yet."
              color="#3B5B92"
            />
            {data.washes.plan_washes > 0 && (
              <p className="mt-3 text-xs text-[#5F6878]">
                {data.washes.plan_washes} of these were plan washes (₹0 here — the plan was paid when sold).
              </p>
            )}
          </Block>
        </section>
      )}

      {data && (
        <Block
          title="Plans Sold"
          subtitle={periodLabel}
          icon={Gift}
          right={
            data.plans.sold > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setDrillTab("plans");
                  setDrillOpen(true);
                }}
                className="inline-flex items-center gap-1 text-xs font-semibold text-[#0A66F0] hover:underline"
              >
                See Who Bought <ArrowRight className="h-3 w-3" />
              </button>
            ) : (
              <Link to="/manager/sell-plan" className="inline-flex items-center gap-1 text-xs font-semibold text-[#0A66F0] hover:underline">
                Sell A Plan <ArrowRight className="h-3 w-3" />
              </Link>
            )
          }
        >
          {data.plans.items.length === 0 ? (
            <p className="text-sm text-[#5F6878]">No plans sold {periodKey === "today" ? "today" : "in this period"}.</p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {data.plans.items.map((p) => (
                <div key={p.plan_id} className="flex items-center justify-between gap-3 rounded-[12px] bg-[#F7F9FC] px-3.5 py-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-[#0E1A33]">{toTitle(p.name)}</p>
                    <p className="text-xs text-[#5F6878]">{formatINR(p.revenue)}</p>
                  </div>
                  <span className="font-mono-num text-xl font-bold text-[#0E1A33]">{p.sold}</span>
                </div>
              ))}
            </div>
          )}
        </Block>
      )}

      {/* 4. Today */}
      {data && (
        <section className="grid grid-cols-1 gap-4 xl:grid-cols-5">
          <Block
            className="xl:col-span-3"
            title="Today's Schedule"
            subtitle={
              data.today.capacity
                ? `${data.today.visits} of ${data.today.capacity} seats booked · ${data.today.cars} car${data.today.cars === 1 ? "" : "s"}`
                : `${data.today.cars} car${data.today.cars === 1 ? "" : "s"} booked`
            }
            icon={CalendarClock}
            right={
              <Link to="/manager/bookings" className="inline-flex items-center gap-1 text-xs font-semibold text-[#0A66F0] hover:underline">
                Booking Queue <ArrowRight className="h-3 w-3" />
              </Link>
            }
          >
            <SlotBoard today={data.today} />
          </Block>
          <Block
            className="xl:col-span-2"
            title="Captains"
            subtitle={`${data.captains.counts.available} free · ${data.captains.counts.on_job} on a job · ${data.captains.counts.total} on team`}
            icon={Users}
            right={
              <Link to="/manager/captains" className="inline-flex items-center gap-1 text-xs font-semibold text-[#0A66F0] hover:underline">
                Manage <ArrowRight className="h-3 w-3" />
              </Link>
            }
          >
            <CaptainList captains={data.captains} />
          </Block>
        </section>
      )}

      {/* 5. Money collected + quality */}
      <CollectionsReportCard
        title="Collections By Captain"
        entityLabel="Captain"
        queryKey={`center-collections-${centerId}`}
        fetcher={(p) => paymentApi.centerCollections(centerId, p)}
      />

      {quality && <QualityStrip q={quality} />}

      <RevenueDrillModal
        open={drillOpen}
        onClose={() => setDrillOpen(false)}
        params={params}
        defaultTab={drillTab || (revenueScope === "plans" ? "plans" : "bookings")}
        serviceCenterId={centerId}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */

function SkeletonRow({ tall = false }: { tall?: boolean }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className={`${tall ? "h-[120px]" : "h-[84px]"} animate-pulse rounded-[14px] border border-[#E4E9F1] bg-[#F7F9FC]`} />
      ))}
    </div>
  );
}

function ChipGroup({
  options,
  value,
  onChange,
  busy,
  small = false,
}: {
  options: { key: string; label: string }[];
  value: string;
  onChange: (key: string) => void;
  busy?: boolean;
  small?: boolean;
}) {
  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${busy ? "opacity-70" : ""}`} role="radiogroup">
      {options.map((o) => {
        const on = o.key === value;
        return (
          <button
            key={o.key}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.key)}
            className={`rounded-full border font-semibold transition-colors ${small ? "px-2.5 py-1 text-[11px]" : "px-3 py-1.5 text-xs"} ${
              on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#5F6878] hover:border-[#C9D6EA] hover:text-[#0E1A33]"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function Block({
  title,
  subtitle,
  icon: Icon,
  right,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-[16px] border border-[#E4E9F1] bg-white ${className}`}>
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-[#EEF2F7] px-4 py-3.5 sm:px-5">
        <div className="flex min-w-0 items-start gap-2.5">
          {Icon && (
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[#EEF3FA] text-[#0E1A33]">
              <Icon className="h-3.5 w-3.5" />
            </span>
          )}
          <div className="min-w-0">
            <h3 className="font-semibold text-[#0E1A33]">{title}</h3>
            {subtitle && <p className="text-xs text-[#5F6878]">{subtitle}</p>}
          </div>
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </header>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

const ALERT_TONE = {
  red: { box: "border-[#FAD4D4] bg-[#FFF5F5]", icon: "bg-[#FDE2E2] text-[#DC2626]", num: "text-[#B42318]" },
  amber: { box: "border-[#F8E2B5] bg-[#FFFAEB]", icon: "bg-[#FDEBC8] text-[#B45309]", num: "text-[#93370D]" },
  blue: { box: "border-[#D6E4FB] bg-[#F5F9FF]", icon: "bg-[#E8F0FE] text-[#0A66F0]", num: "text-[#0E1A33]" },
};

function AttentionStrip({ ops, today }: { ops: ManagerDashboard["ops"]; today: ManagerDashboard["today"] }) {
  const items: { key: string; label: string; hint: string; value: number; icon: LucideIcon; tone: keyof typeof ALERT_TONE; to: string }[] = [
    {
      key: "captain",
      label: "Needs A Captain",
      hint: today.unassigned > 0 ? `${today.unassigned} of them today` : "Assign before the slot starts",
      value: ops.needs_captain,
      icon: UserX,
      tone: "amber",
      to: "/manager/bookings",
    },
    { key: "late", label: "Late To Start", hint: "Captain hasn't left yet", value: ops.late_starts, icon: Timer, tone: "red", to: "/manager/bookings" },
    { key: "issues", label: "Flagged Bookings", hint: "Open issues to resolve", value: ops.open_issues, icon: AlertTriangle, tone: "red", to: "/manager/bookings" },
    { key: "complaints", label: "Open Complaints", hint: "Waiting for a reply", value: ops.open_complaints, icon: AlertTriangle, tone: "amber", to: "/manager/complaints" },
    { key: "stock", label: "Low Stock", hint: "Reorder soon", value: ops.low_stock, icon: Package, tone: "blue", to: "/manager/inventory" },
  ];
  const live = items.filter((i) => i.value > 0);
  if (!live.length) {
    return (
      <div className="flex items-center gap-3 rounded-[14px] border border-[#CDEBD6] bg-[#F3FBF5] px-4 py-3 text-sm text-[#166534]">
        <CheckCircle2 className="h-5 w-5 shrink-0 text-[#16A34A]" />
        All clear — every booking has a captain and nothing is flagged.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(250px,1fr))]">
        {live.map((i) => {
          const t = ALERT_TONE[i.tone];
          return (
            <Link key={i.key} to={i.to} className={`group flex items-center gap-3 rounded-[14px] border p-3.5 transition hover:shadow-[0_6px_18px_-12px_rgba(14,26,51,0.35)] ${t.box}`}>
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${t.icon}`}>
                <i.icon className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-semibold text-[#0E1A33]">{i.label}</span>
                <span className="block truncate text-xs text-[#5F6878]">{i.hint}</span>
              </span>
              <span className={`font-mono-num text-2xl font-bold ${t.num}`}>{i.value}</span>
            </Link>
          );
        })}
      </div>
      {ops.issues.length > 0 && (
        <div className="rounded-[14px] border border-[#E4E9F1] bg-white">
          <p className="border-b border-[#EEF2F7] px-4 py-2.5 text-xs font-semibold text-[#5F6878]">Flagged — Soonest First</p>
          <ul className="divide-y divide-[#EEF2F7]">
            {ops.issues.map((b) => (
              <li key={b.id}>
                <Link
                  to={`/manager/bookings?highlight=${b.id}`}
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm hover:bg-[#F5F8FC]"
                >
                  <span className="min-w-0 text-[#5F6878]">
                    <span className="font-mono-num font-semibold text-[#0E1A33]">{b.booking_number}</span>
                    {[carAndService(b.vehicle_type_name, b.service_names?.join(", ")), formatDay(b.scheduled_date), b.slot_label]
                      .filter(Boolean)
                      .map((part) => ` · ${part}`)
                      .join("")}
                  </span>
                  <span className="rounded-full bg-[#FFF5F5] px-2.5 py-0.5 text-xs font-semibold text-[#B42318]">
                    {toTitle(ISSUE_LABELS[b.issue_flag] || b.issue_flag)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SalesBlock({
  data,
  scope,
  onScope,
  prevNoun,
  onDrill,
}: {
  data: ManagerDashboard;
  scope: RevenueScope;
  onScope: (s: RevenueScope) => void;
  prevNoun: string;
  onDrill: (tab: "bookings" | "plans" | null) => void;
}) {
  const cur = data.sales.current;
  const prev = data.sales.previous;
  const pick = (b: typeof cur) => (scope === "bookings" ? b.revenue : scope === "plans" ? b.plan_revenue : b.combined_revenue);
  const label = scope === "plans" ? "Plan Revenue" : scope === "bookings" ? "Booking Revenue" : "Total Sales";
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <div className="group col-span-2 flex min-w-0 flex-col rounded-[16px] border border-[#E4E9F1] bg-white p-4 transition hover:border-[#0A66F0] sm:p-5 lg:row-span-2">
        <button type="button" onClick={() => onDrill(null)} className="flex w-full min-w-0 flex-col text-left">
        <div className="flex w-full flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 text-[13px] font-medium text-[#5F6878]">
            <IndianRupee className="h-3.5 w-3.5" /> {label}
          </span>
          <span className="flex items-center gap-1 text-xs font-semibold text-[#0A66F0] opacity-0 transition group-hover:opacity-100 max-sm:opacity-100">
            Details <ArrowRight className="h-3 w-3" />
          </span>
        </div>
        <span className="font-mono-num mt-3 text-[34px] font-bold leading-none text-[#0E1A33]">{formatINR(pick(cur))}</span>
        <span className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-[#5F6878]">
          <DeltaPill current={pick(cur)} previous={pick(prev)} /> vs {prevNoun} ({formatINR(pick(prev))})
        </span>
        <span className="mt-4 grid w-full grid-cols-2 gap-2 border-t border-[#EEF2F7] pt-3 text-xs">
          <span>
            <span className="block text-[#5F6878]">Bookings</span>
            <span className="font-mono-num text-sm font-semibold text-[#0E1A33]">{formatINR(cur.revenue)}</span>
          </span>
          <span>
            <span className="block text-[#5F6878]">Plans</span>
            <span className="font-mono-num text-sm font-semibold text-[#0E1A33]">{formatINR(cur.plan_revenue)}</span>
          </span>
        </span>
        </button>
        <div className="mt-3">
          <ChipGroup small options={REVENUE_SCOPES.map((s) => ({ key: s.key, label: s.label }))} value={scope} onChange={(k) => onScope(k as RevenueScope)} />
        </div>
      </div>
      <Tile icon={ShoppingBag} label="Bookings Made" value={cur.bookings} previous={prev.bookings} prevNoun={prevNoun} onClick={() => onDrill("bookings")} />
      <Tile icon={CheckCircle2} label="Washes Done" value={cur.completed} previous={prev.completed} prevNoun={prevNoun} onClick={() => onDrill("bookings")} />
      <Tile icon={Gift} label="Plans Sold" value={cur.plans_sold} previous={prev.plans_sold} prevNoun={prevNoun} onClick={() => onDrill("plans")} />
      <Tile
        icon={IndianRupee}
        label="Average Wash"
        value={cur.completed ? formatINR(cur.revenue / cur.completed) : "—"}
        hint="Booking revenue ÷ washes done"
      />
    </div>
  );
}

function Tile({
  icon: Icon,
  label,
  value,
  previous,
  prevNoun,
  hint,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
  previous?: number;
  prevNoun?: string;
  hint?: string;
  onClick?: () => void;
}) {
  const body = (
    <>
      <span className="flex items-center gap-1.5 text-[13px] font-medium text-[#5F6878]">
        <Icon className="h-3.5 w-3.5 shrink-0" /> <span className="truncate">{label}</span>
      </span>
      <span className="font-mono-num mt-2.5 text-[22px] font-bold leading-none text-[#0E1A33] sm:text-[26px]">{value}</span>
      {typeof value === "number" && previous != null ? (
        <span className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-[#5F6878]">
          <DeltaPill current={value} previous={previous} /> vs {prevNoun}
        </span>
      ) : hint ? (
        <span className="mt-1.5 text-xs text-[#5F6878]">{hint}</span>
      ) : null}
    </>
  );
  const cls = "flex min-w-0 flex-col rounded-[16px] border border-[#E4E9F1] bg-white p-4 text-left transition sm:p-5";
  return onClick ? (
    <button type="button" onClick={onClick} className={`${cls} hover:border-[#0A66F0]`}>
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  );
}

function BarList({
  rows,
  empty,
  color,
}: {
  rows: { key: string; label: string; tag?: string; value: number; money: number }[];
  empty: string;
  color: string;
}) {
  if (!rows.length) return <p className="py-2 text-sm text-[#5F6878]">{empty}</p>;
  const max = Math.max(...rows.map((r) => r.value), 1);
  return (
    <ul className="space-y-3">
      {rows.map((r) => (
        <li key={r.key} className="min-w-0">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate font-medium text-[#0E1A33]">{r.label}</span>
              {r.tag && <span className="shrink-0 rounded-full bg-[#EEF3FA] px-1.5 py-px text-[10px] font-semibold text-[#5F6878]">{r.tag}</span>}
            </span>
            <span className="shrink-0 text-xs text-[#5F6878]">
              <span className="font-mono-num text-sm font-bold text-[#0E1A33]">{r.value}</span> · {formatINR(r.money)}
            </span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-[#EEF3FA]">
            <div className="h-full rounded-full" style={{ width: `${Math.max((r.value / max) * 100, 3)}%`, background: color }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function SlotBoard({ today }: { today: ManagerDashboard["today"] }) {
  if (!today.slots.length) return <p className="text-sm text-[#5F6878]">No slots are set up for today.</p>;
  const legend = [
    { label: "Done", color: C.green },
    { label: "In Progress", color: C.blue },
    { label: "Captain Assigned", color: "#8FA3C4" },
    { label: "Needs A Captain", color: "#F59E0B" },
  ];
  return (
    <div className="space-y-3">
      {today.slots.map((s) => {
        const assigned = Math.max(s.cars - s.completed - s.in_progress - s.unassigned, 0);
        const scale = Math.max(s.capacity || 0, s.cars, 1);
        const seg = (n: number) => `${(n / scale) * 100}%`;
        return (
          <div key={s.key}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
              <span className="font-medium text-[#0E1A33]">
                {s.label}
                {s.is_closed && <span className="ml-2 rounded-full bg-[#F3F4F6] px-1.5 py-px text-[10px] font-semibold text-[#5F6878]">Closed</span>}
              </span>
              <span className="text-xs text-[#5F6878]">
                <span className="font-mono-num font-bold text-[#0E1A33]">{s.cars}</span> car{s.cars === 1 ? "" : "s"}
                {s.capacity ? (
                  <>
                    {" "}
                    · {s.visits}/{s.capacity} seats
                  </>
                ) : null}
                {s.unassigned > 0 && <span className="font-semibold text-[#B45309]"> · {s.unassigned} need a captain</span>}
              </span>
            </div>
            <div className="mt-1.5 flex h-2.5 overflow-hidden rounded-full bg-[#EEF3FA]">
              <div style={{ width: seg(s.completed), background: C.green }} />
              <div style={{ width: seg(s.in_progress), background: C.blue }} />
              <div style={{ width: seg(assigned), background: "#8FA3C4" }} />
              <div style={{ width: seg(s.unassigned), background: "#F59E0B" }} />
            </div>
          </div>
        );
      })}
      <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1">
        {legend.map((l) => (
          <span key={l.label} className="flex items-center gap-1.5 text-[11px] text-[#5F6878]">
            <span className="h-2 w-2 rounded-full" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}

const CAPTAIN_STATE: Record<string, { label: string; cls: string }> = {
  on_job: { label: "On A Job", cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  available: { label: "Free", cls: "bg-[#E7F6EC] text-[#15803D]" },
  not_checked_in: { label: "Not Checked In", cls: "bg-[#F3F4F6] text-[#5F6878]" },
  checked_out: { label: "Checked Out", cls: "bg-[#F3F4F6] text-[#5F6878]" },
  on_leave: { label: "On Leave", cls: "bg-[#FFF6E0] text-[#B45309]" },
};

function CaptainList({ captains }: { captains: ManagerDashboard["captains"] }) {
  if (!captains.items.length) {
    return (
      <p className="text-sm text-[#5F6878]">
        No captains on this center yet.{" "}
        <Link to="/manager/captains" className="font-semibold text-[#0A66F0] hover:underline">
          Add One
        </Link>
      </p>
    );
  }
  return (
    <ul className="max-h-[340px] divide-y divide-[#EEF2F7] overflow-y-auto">
      {captains.items.map((c) => {
        const st = CAPTAIN_STATE[c.state] || CAPTAIN_STATE.not_checked_in;
        return (
          <li key={c.id} className="flex items-center gap-3 py-2.5 first:pt-0">
            {c.photo_url ? (
              <img src={c.photo_url} alt="" className="h-9 w-9 shrink-0 rounded-full object-cover" />
            ) : (
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#EEF3FA] text-sm font-bold text-[#0E1A33]">
                {c.name.charAt(0).toUpperCase()}
              </span>
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-[#0E1A33]">{c.name}</span>
              <span className="block truncate text-xs text-[#5F6878]">
                {c.current_booking ? `Now: ${c.current_booking} · ` : ""}
                {c.done_today}/{c.jobs_today} jobs done today
              </span>
            </span>
            <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${st.cls}`}>{st.label}</span>
          </li>
        );
      })}
    </ul>
  );
}

function QualityStrip({ q }: { q: Record<string, number | null> }) {
  const items = [
    { label: "Customer Rating", value: q.avg_rating != null ? `${q.avg_rating}★` : "—", to: "/manager/reviews" },
    { label: "On-Time Starts", value: q.on_time_pct != null ? `${q.on_time_pct}%` : "—" },
    { label: "Avg Travel", value: q.avg_travel_minutes != null ? `${q.avg_travel_minutes} min` : "—" },
    { label: "Avg Wash Time", value: q.avg_service_minutes != null ? `${q.avg_service_minutes} min` : "—" },
  ];
  return (
    <Block title="Service Quality" subtitle="Averages across this center's captain jobs" icon={CheckCircle2}>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        {items.map((d) => (
          <div key={d.label}>
            <dt className="text-xs text-[#5F6878]">{d.label}</dt>
            <dd className="font-mono-num mt-0.5 text-lg font-bold text-[#0E1A33]">
              {d.to ? (
                <Link to={d.to} className="hover:text-[#0A66F0]">
                  {d.value}
                </Link>
              ) : (
                d.value
              )}
            </dd>
          </div>
        ))}
      </dl>
    </Block>
  );
}
