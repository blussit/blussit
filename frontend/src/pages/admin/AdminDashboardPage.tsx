/**
 * Admin analytics dashboard — the KPI/analytics area only (sidebar,
 * header, nav and design language untouched).
 *
 * Hierarchy: PRIMARY (6 cards answering "how is the business, right
 * now?") -> Action Required (only triggered exceptions) -> SECONDARY
 * (one tab at a time: Business / Customers / Captains / Financial /
 * Marketing / Operations / Areas). One global period filter drives
 * everything; every number compares against the previous equal-length
 * period.
 */
import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, IndianRupee, Percent, Repeat, ShoppingBag, SlidersHorizontal, Sparkles, UserPlus } from "lucide-react";
import { kpiApi, adminUserApi } from "../../api/admin";
import { bookingApi } from "../../api/booking";
import { Card, CardBody, PageLoader, Panel, StatCard, StatusBadge } from "../../components/ui";
import { DatePicker } from "../../components/ui/DatePicker";
import { DeltaPill, InfoTip, TargetChip, formatINR } from "../../components/admin/kpi/charts";
import { AreasTab, BusinessTab, CaptainsTab, CustomersTab, FinancialTab, MarketingTab, OperationsTab } from "../../components/admin/kpi/sections";
import { KpiListModal } from "../../components/admin/kpi/KpiListModal";
import { KpiBriefModal } from "../../components/admin/kpi/KpiBriefModal";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { format, formatSlot } from "../../lib/date";
import type { Booking, User } from "../../types";

const PERIODS = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d", label: "7 days" },
  { key: "30d", label: "30 days" },
  { key: "this_month", label: "This month" },
  { key: "last_month", label: "Last month" },
] as const;

const TABS = [
  { key: "business", label: "Business" },
  { key: "customers", label: "Customers" },
  { key: "captains", label: "Captains" },
  { key: "financial", label: "Financial" },
  { key: "marketing", label: "Marketing" },
  { key: "operations", label: "Operations" },
  { key: "areas", label: "Areas" },
] as const;

type OverviewBlock = {
  bookings: number; completed: number; revenue: number; completion_rate: number | null; repeat_customer_rate: number | null;
  new_customers: number;
  /** Money paid for subscription plans in the period — separate from booking revenue. */
  plan_revenue: number;
  /** revenue + plan_revenue — the one number that's "everything we took in". */
  combined_revenue: number;
};
type OverviewData = {
  current: OverviewBlock;
  previous: OverviewBlock;
  targets: { repeat_rate_pct: number };
  alerts: { severity: string; text: string }[];
};

type RevenueScope = "combined" | "bookings" | "plans";
const REVENUE_SCOPES: { key: RevenueScope; label: string }[] = [
  { key: "combined", label: "Bookings + Plans" },
  { key: "bookings", label: "Bookings only" },
  { key: "plans", label: "Plans only" },
];

export default function AdminDashboardPage() {
  const [periodKey, setPeriodKey] = useState<string>("today");
  const [custom, setCustom] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const [showCustom, setShowCustom] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("business");
  const [revenueScope, setRevenueScope] = useState<RevenueScope>("combined");
  // Which primary tile's drill-down is open — a real list for a count that
  // maps to actual records, a brief popup (definition + numbers already on
  // screen) for a ratio. Only one of these two is ever non-null at once.
  const [listDrill, setListDrill] = useState<"bookings-created" | "bookings-completed" | "new-customers" | null>(null);
  const [briefDrill, setBriefDrill] = useState<{ title: string; value: ReactNode; tip: string; breakdown?: { label: string; value: ReactNode }[] } | null>(null);
  const [openBooking, setOpenBooking] = useState<Booking | null>(null);
  const [openCustomerId, setOpenCustomerId] = useState<string | null>(null);

  const params = useMemo(
    () => (periodKey === "custom" && custom.start && custom.end ? { start: custom.start, end: custom.end } : { period: periodKey === "custom" ? "30d" : periodKey }),
    [periodKey, custom],
  );

  const { data, isLoading } = useQuery({
    queryKey: ["kpi", "overview", params],
    queryFn: () => kpiApi.section<OverviewData>("overview", params),
  });

  if (isLoading && !data) return <PageLoader />;

  const periodNoun = periodKey === "today" ? "yesterday" : "previous period";
  const cur = data?.current;
  const prev = data?.previous;
  const alerts = data?.alerts || [];

  const revenueFor = (block?: OverviewBlock) =>
    !block ? 0 : revenueScope === "bookings" ? block.revenue : revenueScope === "plans" ? block.plan_revenue : block.combined_revenue;
  const revenueLabel =
    (periodKey === "today" ? "Today's revenue" : "Revenue") +
    (revenueScope === "bookings" ? " — bookings" : revenueScope === "plans" ? " — plans" : "");
  const revenueTip =
    revenueScope === "bookings"
      ? "Completed-wash revenue in the period"
      : revenueScope === "plans"
        ? "Money paid for subscription plans in the period"
        : "Completed-wash revenue + plan/subscription revenue in the period — everything taken in";

  const primary = cur && prev ? [
    {
      label: periodKey === "today" ? "Today's bookings" : "Bookings", value: String(cur.bookings), icon: ShoppingBag,
      delta: <DeltaPill current={cur.bookings} previous={prev.bookings} />, tip: "Bookings created in the selected period",
      onClick: () => setListDrill("bookings-created"),
    },
    {
      label: "Completed washes", value: String(cur.completed), icon: CheckCircle2,
      delta: <DeltaPill current={cur.completed} previous={prev.completed} />, tip: "Washes finished in the selected period",
      onClick: () => setListDrill("bookings-completed"),
    },
    {
      label: revenueLabel, value: formatINR(revenueFor(cur)), icon: IndianRupee,
      delta: <DeltaPill current={revenueFor(cur)} previous={revenueFor(prev)} />, tip: revenueTip,
      onClick: () =>
        revenueScope === "plans"
          ? setBriefDrill({
              title: "Plan revenue", value: formatINR(cur.plan_revenue), tip: revenueTip,
              breakdown: [{ label: "vs previous period", value: formatINR(prev.plan_revenue) }],
            })
          : setListDrill("bookings-completed"),
    },
    {
      label: "Completion rate", value: cur.completion_rate == null ? "—" : `${cur.completion_rate}%`, icon: Percent,
      delta: <DeltaPill current={cur.completion_rate} previous={prev.completion_rate} />, tip: "Completed ÷ all bookings",
      onClick: () =>
        setBriefDrill({
          title: "Completion rate", value: cur.completion_rate == null ? "—" : `${cur.completion_rate}%`, tip: "Completed ÷ all bookings in the period",
          breakdown: [
            { label: "Bookings created", value: cur.bookings },
            { label: "Completed", value: cur.completed },
          ],
        }),
    },
    {
      label: "New customers", value: String(cur.new_customers), icon: UserPlus,
      delta: <DeltaPill current={cur.new_customers} previous={prev.new_customers} />, tip: "Customer accounts created in the period",
      onClick: () => setListDrill("new-customers"),
    },
    {
      label: "Repeat customer rate", value: cur.repeat_customer_rate == null ? "—" : `${cur.repeat_customer_rate}%`, icon: Repeat,
      delta: <DeltaPill current={cur.repeat_customer_rate} previous={prev.repeat_customer_rate} />, tip: "Share of this period's customers who had booked before it",
      target: <TargetChip actual={cur.repeat_customer_rate} target={data?.targets?.repeat_rate_pct} unit="%" />,
      onClick: () =>
        setBriefDrill({
          title: "Repeat customer rate", value: cur.repeat_customer_rate == null ? "—" : `${cur.repeat_customer_rate}%`,
          tip: "Share of this period's customers who had booked before it started — a rising rate means BLUSSIT is becoming a habit, not a one-time purchase.",
        }),
    },
  ] : [];

  // Revenue leads (it is THE business number); the rest support it.
  const hero = primary.find((p) => p.label.startsWith(periodKey === "today" ? "Today's revenue" : "Revenue"));
  const rest = primary.filter((p) => p !== hero);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-black">Analytics</h1>
          <p className="mt-1 text-sm text-gray-500">Business health, compared with {periodNoun}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => { setPeriodKey(p.key); setShowCustom(false); }}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${periodKey === p.key ? "bg-black text-white" : "border border-[#F3E5B5] bg-white text-gray-600 hover:border-black"}`}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => { setShowCustom((v) => !v); if (custom.start && custom.end) setPeriodKey("custom"); }}
            className={`inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${periodKey === "custom" ? "bg-black text-white" : "border border-[#F3E5B5] bg-white text-gray-600 hover:border-black"}`}
          >
            <SlidersHorizontal className="h-3 w-3" /> Custom
          </button>
        </div>
      </div>

      {showCustom && (
        <Card>
          <CardBody className="flex flex-wrap items-end gap-3 p-4">
            <DatePicker label="From" value={custom.start} onChange={(v) => setCustom((c) => ({ ...c, start: v }))} />
            <DatePicker label="To" value={custom.end} onChange={(v) => setCustom((c) => ({ ...c, end: v }))} />
            <button
              type="button"
              disabled={!custom.start || !custom.end}
              onClick={() => { setPeriodKey("custom"); setShowCustom(false); }}
              className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            >
              Apply
            </button>
          </CardBody>
        </Card>
      )}

      {/* PRIMARY — revenue is the headline, the rest are supporting tiles.
          Same console shape as every other panel in the product. */}
      {hero && (
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
          <StatCard
            label={hero.label}
            labelAfter={<InfoTip text={hero.tip} />}
            value={hero.value}
            hint={
              <span className="flex items-center gap-1.5">
                {hero.delta} <span>vs {periodNoun}</span>
              </span>
            }
            icon={hero.icon}
            onClick={hero.onClick}
            linkLabel="Details"
          />
        </div>
      )}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {rest.map((s) => (
          <StatCard
            key={s.label}
            label={s.label}
            labelAfter={<InfoTip text={s.tip} />}
            value={s.value}
            icon={s.icon}
            hint={
              <span className="flex flex-wrap items-center gap-1.5">
                {s.delta} <span>vs {periodNoun}</span>
                {"target" in s ? s.target : null}
              </span>
            }
            onClick={s.onClick}
            linkLabel="Details"
          />
        ))}
      </div>

      {/* Action required — only triggered exceptions, invisible when healthy. */}
      {alerts.length > 0 ? (
        <Panel title="Action required">
          <ul className="space-y-1.5">
            {alerts.map((a) => (
              <li key={a.text} className="flex items-start gap-2 text-sm text-gray-700">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                {a.text}
              </li>
            ))}
          </ul>
        </Panel>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-gray-400">
          <Sparkles className="h-3.5 w-3.5" /> No exceptions need attention in this period.
        </p>
      )}

      {/* SECONDARY — one focus area at a time. */}
      <div className="overflow-x-auto">
        <div className="flex min-w-max gap-1 rounded-xl border border-[#F3E5B5] bg-white p-1">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${tab === t.key ? "bg-black text-white" : "text-gray-600 hover:bg-[#FFF4CD]"}`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "business" && <BusinessTab params={params} />}
      {tab === "customers" && <CustomersTab params={params} />}
      {tab === "captains" && <CaptainsTab params={params} />}
      {tab === "financial" && <FinancialTab params={params} />}
      {tab === "marketing" && <MarketingTab params={params} />}
      {tab === "operations" && <OperationsTab params={params} />}
      {tab === "areas" && <AreasTab params={params} />}

      <KpiListModal<Booking>
        open={listDrill === "bookings-created" || listDrill === "bookings-completed"}
        onClose={() => setListDrill(null)}
        title={listDrill === "bookings-completed" ? "Completed washes" : "Bookings"}
        queryKey={["kpi-drill", listDrill, params]}
        fetchFn={() =>
          bookingApi.all({
            ...params, page_size: 100,
            date_field: listDrill === "bookings-completed" ? "completed" : "created",
            status: listDrill === "bookings-completed" ? "completed" : undefined,
          })
        }
        getRowKey={(b) => b.id}
        renderRow={(b) => (
          <button type="button" onClick={() => setOpenBooking(b)} className="flex w-full items-center justify-between gap-3 text-left">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-black">{b.customer_name || "—"} · {b.booking_number}</p>
              <p className="text-xs text-gray-500">{format(b.scheduled_date)} · {formatSlot(b.scheduled_slot)}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="font-mono-num text-sm text-black">₹{b.total_amount}</span>
              <StatusBadge status={b.status} />
            </div>
          </button>
        )}
      />

      <KpiListModal<User>
        open={listDrill === "new-customers"}
        onClose={() => setListDrill(null)}
        title="New customers"
        queryKey={["kpi-drill-new-customers", params]}
        fetchFn={() => adminUserApi.list({ ...params, role: "customer", page_size: 100 })}
        getRowKey={(u) => u.id}
        renderRow={(u) => (
          <button type="button" onClick={() => setOpenCustomerId(u.id)} className="flex w-full items-center justify-between gap-3 text-left">
            <span className="text-sm font-medium text-black">{u.full_name}</span>
            <span className="text-xs text-gray-500">{u.phone}</span>
          </button>
        )}
      />

      {briefDrill && (
        <KpiBriefModal open={!!briefDrill} onClose={() => setBriefDrill(null)} title={briefDrill.title} value={briefDrill.value} tip={briefDrill.tip} breakdown={briefDrill.breakdown} />
      )}
      <BookingDetailDrawer booking={openBooking} onClose={() => setOpenBooking(null)} />
      <CustomerDetailDrawer customerId={openCustomerId} onClose={() => setOpenCustomerId(null)} />
    </div>
  );
}
