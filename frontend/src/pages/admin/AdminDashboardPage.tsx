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
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, IndianRupee, Percent, Repeat, ShoppingBag, SlidersHorizontal, Sparkles, UserPlus } from "lucide-react";
import { kpiApi } from "../../api/admin";
import { Card, CardBody, PageLoader } from "../../components/ui";
import { DatePicker } from "../../components/ui/DatePicker";
import { DeltaPill, InfoTip, TargetChip, formatINR } from "../../components/admin/kpi/charts";
import { AreasTab, BusinessTab, CaptainsTab, CustomersTab, FinancialTab, MarketingTab, OperationsTab } from "../../components/admin/kpi/sections";

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

type OverviewData = {
  current: { bookings: number; completed: number; revenue: number; completion_rate: number | null; repeat_customer_rate: number | null; new_customers: number };
  previous: { bookings: number; completed: number; revenue: number; completion_rate: number | null; repeat_customer_rate: number | null; new_customers: number };
  targets: { repeat_rate_pct: number };
  alerts: { severity: string; text: string }[];
};

export default function AdminDashboardPage() {
  const [periodKey, setPeriodKey] = useState<string>("today");
  const [custom, setCustom] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const [showCustom, setShowCustom] = useState(false);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("business");

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

  const primary = cur && prev ? [
    { label: periodKey === "today" ? "Today's bookings" : "Bookings", value: String(cur.bookings), icon: ShoppingBag, delta: <DeltaPill current={cur.bookings} previous={prev.bookings} />, tip: "Bookings created in the selected period" },
    { label: "Completed washes", value: String(cur.completed), icon: CheckCircle2, delta: <DeltaPill current={cur.completed} previous={prev.completed} />, tip: "Washes finished in the selected period" },
    { label: periodKey === "today" ? "Today's revenue" : "Revenue", value: formatINR(cur.revenue), icon: IndianRupee, delta: <DeltaPill current={cur.revenue} previous={prev.revenue} />, tip: "Completed-wash revenue in the period" },
    { label: "Completion rate", value: cur.completion_rate == null ? "—" : `${cur.completion_rate}%`, icon: Percent, delta: <DeltaPill current={cur.completion_rate} previous={prev.completion_rate} />, tip: "Completed ÷ all bookings" },
    { label: "New customers", value: String(cur.new_customers), icon: UserPlus, delta: <DeltaPill current={cur.new_customers} previous={prev.new_customers} />, tip: "Customer accounts created in the period" },
    {
      label: "Repeat customer rate", value: cur.repeat_customer_rate == null ? "—" : `${cur.repeat_customer_rate}%`, icon: Repeat,
      delta: <DeltaPill current={cur.repeat_customer_rate} previous={prev.repeat_customer_rate} />, tip: "Share of this period's customers who had booked before it",
      target: <TargetChip actual={cur.repeat_customer_rate} target={data?.targets?.repeat_rate_pct} unit="%" />,
    },
  ] : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Analytics dashboard</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Business health at a glance — compared with the {periodNoun}.</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => { setPeriodKey(p.key); setShowCustom(false); }}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${periodKey === p.key ? "bg-[var(--color-primary)] text-white" : "bg-white text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"}`}
            >
              {p.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => { setShowCustom((v) => !v); if (custom.start && custom.end) setPeriodKey("custom"); }}
            className={`inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${periodKey === "custom" ? "bg-[var(--color-primary)] text-white" : "bg-white text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"}`}
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

      {/* PRIMARY — the 10-second business-health answer. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {primary.map((s) => (
          <Card key={s.label}>
            <CardBody className="p-4">
              <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">
                <s.icon className="h-3.5 w-3.5" /> {s.label} <InfoTip text={s.tip} />
              </p>
              <p className="mt-1.5 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{s.value}</p>
              <div className="mt-1 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
                {s.delta} <span>vs {periodNoun}</span>
              </div>
              {"target" in s ? s.target : null}
            </CardBody>
          </Card>
        ))}
      </div>

      {/* Action required — only triggered exceptions, invisible when healthy. */}
      {alerts.length > 0 ? (
        <Card className="border-amber-200 bg-amber-50/60">
          <CardBody className="p-4">
            <p className="mb-2 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-amber-800">
              <AlertTriangle className="h-3.5 w-3.5" /> Action required
            </p>
            <ul className="space-y-1">
              {alerts.map((a) => (
                <li key={a.text} className="text-sm text-amber-900">⚠ {a.text}</li>
              ))}
            </ul>
          </CardBody>
        </Card>
      ) : (
        <p className="flex items-center gap-1.5 text-xs text-[var(--color-success)]">
          <Sparkles className="h-3.5 w-3.5" /> No exceptions need attention in this period.
        </p>
      )}

      {/* SECONDARY — one focus area at a time. */}
      <div className="overflow-x-auto">
        <div className="flex min-w-max gap-1 rounded-xl bg-white p-1 shadow-[var(--shadow-soft)]">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${tab === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"}`}
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
    </div>
  );
}
