/**
 * KPI dashboard tab content. Each tab fetches exactly one
 * /analytics/kpis/<section> payload for the globally selected period and
 * lays it out PRIMARY -> SECONDARY -> DETAIL, so no tab ever shows
 * everything at once.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { Card, CardBody, CardHeader, Spinner } from "../../ui";
import { kpiApi, type KpiPeriodParams } from "../../../api/admin";
import { Donut, Funnel, HBars, MiniStat, RatingBars, TrendChart, formatINR } from "./charts";
import { BusinessSettingsModal } from "./SettingsModal";

function useSection<T>(section: string, params: KpiPeriodParams) {
  return useQuery({
    queryKey: ["kpi", section, params],
    queryFn: () => kpiApi.section<T>(section, params),
  });
}

function Loading() {
  return (
    <div className="flex justify-center py-16">
      <Spinner />
    </div>
  );
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${v}%`);
const num = (v: number | null | undefined, unit = "") => (v == null ? "—" : `${v}${unit}`);

/* ------------------------------------------------------------------ */
/* Business                                                            */
/* ------------------------------------------------------------------ */
type BusinessData = {
  totals: { bookings: number; completed: number; cancelled: number; completion_rate: number | null; aov: number; revenue: number; revenue_growth: number | null; booking_growth: number | null };
  trend: { date: string; bookings: number; revenue: number }[];
  service_mix: { name: string; bookings: number; revenue: number; aov: number; cancellation_rate: number | null }[];
  vehicle_mix: { name: string; bookings: number; revenue: number; aov: number }[];
  revenue_quality: { total: number; new_customer_revenue: number; repeat_customer_revenue: number; repeat_revenue_pct: number | null; subscription_revenue: number; one_time_revenue: number };
};

export function BusinessTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<BusinessData>("business", params);
  if (isLoading || !data) return <Loading />;
  const t = data.totals;
  const q = data.revenue_quality;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MiniStat label="Bookings" value={t.bookings} tip="All bookings created in the period" />
        <MiniStat label="Completed" value={t.completed} />
        <MiniStat label="Cancelled" value={t.cancelled} />
        <MiniStat label="Completion rate" value={pct(t.completion_rate)} tip="Completed ÷ all bookings in the period" />
        <MiniStat label="Avg order value" value={formatINR(t.aov)} tip="Revenue ÷ completed washes" />
        <MiniStat label="Growth" value={t.revenue_growth == null ? "—" : `${t.revenue_growth > 0 ? "+" : ""}${t.revenue_growth}%`} tip="Revenue vs the previous equal-length period" />
      </div>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Revenue & booking trend</h3>
        </CardHeader>
        <CardBody>
          <TrendChart data={data.trend} />
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Service mix</h3>
          </CardHeader>
          <CardBody>
            <Donut items={data.service_mix.map((s) => ({ name: s.name, value: s.revenue }))} valueLabel={formatINR} />
            {data.service_mix.length > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-[var(--color-text-secondary)]">
                    <tr><th className="py-1 pr-2 font-medium">Service</th><th className="py-1 pr-2 font-medium">Bookings</th><th className="py-1 pr-2 font-medium">AOV</th><th className="py-1 font-medium">Cancel %</th></tr>
                  </thead>
                  <tbody className="text-[var(--color-text-primary)]">
                    {data.service_mix.map((s) => (
                      <tr key={s.name} className="border-t border-gray-50">
                        <td className="py-1.5 pr-2">{s.name}</td>
                        <td className="py-1.5 pr-2 font-mono-num">{s.bookings}</td>
                        <td className="py-1.5 pr-2 font-mono-num">{formatINR(s.aov)}</td>
                        <td className="py-1.5 font-mono-num">{pct(s.cancellation_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Vehicle types</h3>
            </CardHeader>
            <CardBody>
              <HBars rows={data.vehicle_mix.map((v) => ({ label: v.name, value: v.bookings, sub: `${formatINR(v.revenue)} · AOV ${formatINR(v.aov)}` }))} />
            </CardBody>
          </Card>
          <Card>
            <CardHeader>
              <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Revenue quality</h3>
            </CardHeader>
            <CardBody className="space-y-2 text-sm">
              <Row label="Total revenue" value={formatINR(q.total)} bold />
              <Row label="New-customer revenue" value={formatINR(q.new_customer_revenue)} />
              <Row label="Repeat-customer revenue" value={formatINR(q.repeat_customer_revenue)} />
              <Row label="Repeat revenue share" value={pct(q.repeat_revenue_pct)} bold />
              <Row label="Subscription-covered" value={formatINR(q.subscription_revenue)} />
              <Row label="One-time" value={formatINR(q.one_time_revenue)} />
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, bold }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--color-text-secondary)]">{label}</span>
      <span className={`font-mono-num ${bold ? "font-bold text-[var(--color-text-primary)]" : "text-[var(--color-text-primary)]"}`}>{value}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Customers                                                           */
/* ------------------------------------------------------------------ */
type CustomersData = {
  total_customers: number; new_customers: number; new_customers_growth: number | null;
  repeat_customers: number; repeat_rate: number | null; second_wash_rate: number | null;
  retention: { d30: number | null; d60: number | null; d90: number | null };
  avg_washes_per_customer: number; avg_days_between_washes: number | null;
  churn_rate: number | null; clv: number;
  new_vs_repeat_revenue: { new: number; repeat: number; repeat_pct: number | null };
};

export function CustomersTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<CustomersData>("customers", params);
  if (isLoading || !data) return <Loading />;
  const r = data.retention;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MiniStat label="New customers" value={data.new_customers} tip="Customer accounts created in the period" />
        <MiniStat label="Repeat rate" value={pct(data.repeat_rate)} tip="Customers with more than one lifetime booking" />
        <MiniStat label="Second wash rate" value={pct(data.second_wash_rate)} tip="Of customers whose first wash was 30+ days ago, how many came back for a second" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Retention — booked again within…</h3>
          </CardHeader>
          <CardBody>
            <HBars
              rows={[
                { label: "30 days of first wash", value: r.d30 ?? 0, sub: r.d30 == null ? "(no matured cohort yet)" : "" },
                { label: "60 days of first wash", value: r.d60 ?? 0, sub: r.d60 == null ? "(no matured cohort yet)" : "" },
                { label: "90 days of first wash", value: r.d90 ?? 0, sub: r.d90 == null ? "(no matured cohort yet)" : "" },
              ]}
              valueLabel={(v) => `${v}%`}
            />
            <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
              Each row only counts customers whose first wash is old enough to be judged fairly.
            </p>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">New vs repeat revenue (this period)</h3>
          </CardHeader>
          <CardBody>
            <Donut
              items={[
                { name: "New customers", value: data.new_vs_repeat_revenue.new },
                { name: "Repeat customers", value: data.new_vs_repeat_revenue.repeat },
              ]}
              valueLabel={formatINR}
            />
            <p className="mt-3 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
              A rising repeat share means BLUSSIT is becoming a habit, not a one-time purchase.
            </p>
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <MiniStat label="Total customers" value={data.total_customers} />
        <MiniStat label="Avg washes / customer" value={num(data.avg_washes_per_customer)} />
        <MiniStat label="Avg days between washes" value={num(data.avg_days_between_washes)} />
        <MiniStat label="Churn" value={pct(data.churn_rate)} tip="Customers whose last wash was more than 60 days ago" />
        <MiniStat label="Lifetime value" value={formatINR(data.clv)} tip="Average completed revenue per booking customer, all time" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Captains                                                            */
/* ------------------------------------------------------------------ */
type CaptainsData = {
  days_in_period: number;
  target_washes_per_captain_per_day: number;
  fleet_washes_per_captain_per_day: number;
  captains: { captain_id: string; name: string; jobs: number; washes_per_day: number; revenue: number; avg_job_minutes: number | null; avg_travel_minutes: number | null; on_time_pct: number | null; rating: number | null; complaints: number; cancellations: number; utilisation_pct: number | null }[];
};

export function CaptainsTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<CaptainsData>("captains", params);
  const [openId, setOpenId] = useState<string | null>(null);
  if (isLoading || !data) return <Loading />;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MiniStat label="Washes / captain / day" value={num(data.fleet_washes_per_captain_per_day)} tip="THE core operational KPI — fleet average for the period">
          <p className="mt-1 text-[10px] text-[var(--color-text-secondary)]">Target: {data.target_washes_per_captain_per_day}/day</p>
        </MiniStat>
        <MiniStat label="Active captains" value={data.captains.length} />
        <MiniStat label="Days in period" value={data.days_in_period} />
      </div>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Captain comparison</h3>
        </CardHeader>
        <CardBody className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs text-[var(--color-text-secondary)]">
              <tr>
                <th className="py-2 pr-3 font-medium">Captain</th>
                <th className="py-2 pr-3 font-medium">Jobs</th>
                <th className="py-2 pr-3 font-medium">/day</th>
                <th className="py-2 pr-3 font-medium">Revenue</th>
                <th className="py-2 pr-3 font-medium">Rating</th>
                <th className="py-2 pr-3 font-medium">On-time</th>
                <th className="py-2 pr-3 font-medium">Utilisation</th>
              </tr>
            </thead>
            <tbody className="text-[var(--color-text-primary)]">
              {data.captains.map((c) => (
                <>
                  <tr key={c.captain_id} className="cursor-pointer border-t border-gray-50 hover:bg-[var(--color-surface)]" onClick={() => setOpenId(openId === c.captain_id ? null : c.captain_id)}>
                    <td className="py-2.5 pr-3 font-medium">{c.name}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{c.jobs}</td>
                    <td className="py-2.5 pr-3 font-mono-num font-semibold">{c.washes_per_day}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{formatINR(c.revenue)}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{c.rating ? `${c.rating}★` : "—"}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{pct(c.on_time_pct)}</td>
                    <td className="py-2.5 pr-3">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded-full bg-black/[0.08]">
                          <div className={`h-1.5 rounded-full ${(c.utilisation_pct ?? 0) >= 80 ? "bg-[var(--color-success)]" : "bg-amber-400"}`} style={{ width: `${Math.min(c.utilisation_pct ?? 0, 100)}%` }} />
                        </div>
                        <span className="font-mono-num text-xs">{pct(c.utilisation_pct)}</span>
                      </div>
                    </td>
                  </tr>
                  {openId === c.captain_id && (
                    <tr key={`${c.captain_id}-detail`} className="border-t border-gray-50 bg-[var(--color-surface)]">
                      <td colSpan={7} className="px-3 py-3">
                        <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                          <span>Avg job: <b className="font-mono-num">{num(c.avg_job_minutes, " min")}</b></span>
                          <span>Avg travel: <b className="font-mono-num">{num(c.avg_travel_minutes, " min")}</b></span>
                          <span>Complaints: <b className="font-mono-num">{c.complaints}</b></span>
                          <span>Cancellations: <b className="font-mono-num">{c.cancellations}</b></span>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
          {!data.captains.length && <p className="py-6 text-center text-sm text-[var(--color-text-secondary)]">No captains yet.</p>}
        </CardBody>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Financial                                                           */
/* ------------------------------------------------------------------ */
type FinancialData = {
  inputs: { variable_cost_per_wash: number; fixed_cost_monthly: number; kit_cost: number; kits_count: number };
  gross_revenue: number; washes: number; aov: number; variable_cost: number; contribution: number;
  contribution_per_wash: number; contribution_margin_pct: number | null; fixed_cost_period: number;
  marketing_spend: number; net_profit: number; profit_margin_pct: number | null;
  break_even_washes_per_day: number | null; actual_washes_per_day: number; break_even_revenue_per_day: number | null;
  revenue_per_captain: number; kit_payback_months: number | null; cost_per_booking: number;
};

export function FinancialTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<FinancialData>("financial", params);
  const [editOpen, setEditOpen] = useState(false);
  const qc = useQueryClient();
  if (isLoading || !data) return <Loading />;
  const profitable = data.net_profit >= 0;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--color-text-secondary)]">
          Costs below come from your own inputs — keep them honest and this page tells the truth.
        </p>
        <button type="button" onClick={() => setEditOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface)]">
          <Pencil className="h-3 w-3" /> Edit cost inputs
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MiniStat label="Revenue" value={formatINR(data.gross_revenue)} />
        <MiniStat label="Operating cost" value={formatINR(data.variable_cost + data.fixed_cost_period)} tip="Variable cost × washes + prorated fixed cost" />
        <MiniStat label="Contribution margin" value={pct(data.contribution_margin_pct)} tip="(Revenue − variable costs) ÷ revenue" />
        <MiniStat label="Net profit" value={<span className={profitable ? "text-[var(--color-success)]" : "text-[var(--color-error)]"}>{formatINR(data.net_profit)}</span>} tip="Contribution − fixed costs − marketing spend" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Unit economics (per wash)</h3>
          </CardHeader>
          <CardBody>
            <ol className="space-y-1">
              {[
                { label: "Average booking", value: data.aov },
                { label: "− Variable cost", value: -data.inputs.variable_cost_per_wash },
                { label: "= Contribution / wash", value: data.contribution_per_wash, strong: true },
              ].map((row) => (
                <li key={row.label} className={`flex items-baseline justify-between rounded-lg px-3 py-2 ${row.strong ? "bg-[var(--color-primary)] font-bold text-white [&_span]:!text-white" : "bg-[var(--color-surface)]"}`}>
                  <span className="text-sm text-[var(--color-text-primary)]">{row.label}</span>
                  <span className={`font-mono-num text-sm ${row.value < 0 ? "text-[var(--color-error)]" : "text-[var(--color-text-primary)]"}`}>{formatINR(Math.abs(row.value))}</span>
                </li>
              ))}
            </ol>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <MiniStat label="Revenue / captain" value={formatINR(data.revenue_per_captain)} />
              <MiniStat label="Cost / booking" value={formatINR(data.cost_per_booking)} />
              <MiniStat label="Kit payback" value={data.kit_payback_months == null ? "—" : `${data.kit_payback_months} mo`} tip="Kit cost ÷ monthly contribution per kit, at this period's pace" />
              <MiniStat label="Marketing spend" value={formatINR(data.marketing_spend)} />
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Break-even</h3>
          </CardHeader>
          <CardBody>
            {data.break_even_washes_per_day == null ? (
              <p className="py-6 text-center text-sm text-[var(--color-text-secondary)]">
                Set your monthly fixed cost (Edit cost inputs) to see the break-even line.
              </p>
            ) : (
              <div>
                <div className="flex items-baseline justify-between">
                  <div>
                    <p className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">{data.actual_washes_per_day}</p>
                    <p className="text-xs text-[var(--color-text-secondary)]">actual washes / day</p>
                  </div>
                  <div className="text-right">
                    <p className="font-mono-num text-3xl font-bold text-[var(--color-text-secondary)]">{data.break_even_washes_per_day}</p>
                    <p className="text-xs text-[var(--color-text-secondary)]">needed to break even</p>
                  </div>
                </div>
                <div className="mt-4 h-2.5 rounded-full bg-black/[0.08]">
                  <div
                    className={`h-2.5 rounded-full ${data.actual_washes_per_day >= data.break_even_washes_per_day ? "bg-[var(--color-success)]" : "bg-amber-400"}`}
                    style={{ width: `${Math.min((data.actual_washes_per_day / data.break_even_washes_per_day) * 100, 100)}%` }}
                  />
                </div>
                <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                  {data.actual_washes_per_day >= data.break_even_washes_per_day
                    ? "Above break-even at current pace."
                    : `Needs ${(data.break_even_washes_per_day - data.actual_washes_per_day).toFixed(1)} more washes/day to break even${data.break_even_revenue_per_day ? ` (${formatINR(data.break_even_revenue_per_day)}/day)` : ""}.`}
                </p>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <BusinessSettingsModal open={editOpen} onClose={() => { setEditOpen(false); qc.invalidateQueries({ queryKey: ["kpi"] }); }} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Marketing                                                           */
/* ------------------------------------------------------------------ */
type MarketingData = {
  spend: number; leads: number; coverage_leads: number; cost_per_lead: number | null;
  new_customers: number; ad_attributed_customers: number; referral_customers: number; organic_customers: number;
  referral_rate: number | null; cac: number | null; ad_attributed_revenue: number; roas: number | null;
  booking_conversion_pct: number | null;
  funnel: { leads: number; bookings: number; completed: number; repeat_customers: number };
  by_source: { source: string; spend: number; leads: number; customers: number; revenue: number }[];
  campaigns: { date: string; source: string; campaign: string; spend: number; leads: number; customers: number; revenue: number; cac: number | null; roas: number | null }[];
  target_cac: number | null;
};

export function MarketingTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<MarketingData>("marketing", params);
  const [editOpen, setEditOpen] = useState(false);
  const qc = useQueryClient();
  if (isLoading || !data) return <Loading />;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--color-text-secondary)]">
          Spend, leads and attribution are entered by you per campaign; coverage-area requests are counted automatically.
        </p>
        <button type="button" onClick={() => setEditOpen(true)} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-surface)]">
          <Pencil className="h-3 w-3" /> Log spend / campaigns
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MiniStat label="Spend" value={formatINR(data.spend)} />
        <MiniStat label="Leads" value={data.leads} tip={`Includes ${data.coverage_leads} automatic coverage-area requests`} />
        <MiniStat label="Cost / lead" value={data.cost_per_lead == null ? "—" : formatINR(data.cost_per_lead)} />
        <MiniStat label="CAC" value={data.cac == null ? "—" : formatINR(data.cac)} tip="Spend ÷ new customers acquired" />
        <MiniStat label="ROAS" value={data.roas == null ? "—" : `${data.roas}x`} tip="Ad-attributed revenue ÷ spend" />
        <MiniStat label="Referral rate" value={pct(data.referral_rate)} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Funnel</h3>
          </CardHeader>
          <CardBody>
            <Funnel
              steps={[
                { label: "Leads", value: data.funnel.leads },
                { label: "Bookings", value: data.funnel.bookings },
                { label: "Completed washes", value: data.funnel.completed },
                { label: "Repeat customers", value: data.funnel.repeat_customers },
              ]}
            />
            <div className="mt-4 grid grid-cols-3 gap-3">
              <MiniStat label="From ads" value={data.ad_attributed_customers} />
              <MiniStat label="Referral" value={data.referral_customers} />
              <MiniStat label="Organic" value={data.organic_customers} />
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Campaigns</h3>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            {data.campaigns.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--color-text-secondary)]">No spend logged in this period — use "Log spend / campaigns".</p>
            ) : (
              <table className="w-full min-w-[520px] text-left text-xs">
                <thead className="text-[var(--color-text-secondary)]">
                  <tr>
                    <th className="py-1.5 pr-2 font-medium">Campaign</th>
                    <th className="py-1.5 pr-2 font-medium">Source</th>
                    <th className="py-1.5 pr-2 font-medium">Spend</th>
                    <th className="py-1.5 pr-2 font-medium">Leads</th>
                    <th className="py-1.5 pr-2 font-medium">Customers</th>
                    <th className="py-1.5 pr-2 font-medium">CAC</th>
                    <th className="py-1.5 font-medium">ROAS</th>
                  </tr>
                </thead>
                <tbody className="text-[var(--color-text-primary)]">
                  {data.campaigns.map((c, i) => (
                    <tr key={`${c.campaign}-${i}`} className="border-t border-gray-50">
                      <td className="py-2 pr-2">{c.campaign}</td>
                      <td className="py-2 pr-2 capitalize">{c.source}</td>
                      <td className="py-2 pr-2 font-mono-num">{formatINR(c.spend)}</td>
                      <td className="py-2 pr-2 font-mono-num">{c.leads}</td>
                      <td className="py-2 pr-2 font-mono-num">{c.customers}</td>
                      <td className="py-2 pr-2 font-mono-num">{c.cac == null ? "—" : formatINR(c.cac)}</td>
                      <td className="py-2 font-mono-num">{c.roas == null ? "—" : `${c.roas}x`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardBody>
        </Card>
      </div>

      <BusinessSettingsModal open={editOpen} onClose={() => { setEditOpen(false); qc.invalidateQueries({ queryKey: ["kpi"] }); }} initialTab="marketing" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Operations (capacity + experience + quality)                        */
/* ------------------------------------------------------------------ */
type OperationsData = {
  capacity: { total: number; booked: number; utilisation_pct: number | null; available: number; peak_slot: string | null; lowest_slot: string | null };
  on_time_arrival_pct: number | null; avg_travel_minutes: number | null; avg_service_minutes: number | null;
  cancellation_rate: number | null; captain_cancellations: number;
  experience: { avg_rating: number | null; five_star_pct: number | null; rating_distribution: Record<string, number>; review_collection_pct: number | null; complaints: number; complaint_rate_pct: number | null; unresolved_complaints: number; avg_resolution_hours: number | null; complaint_categories: { category: string; count: number }[] };
};

export function OperationsTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<OperationsData>("operations", params);
  if (isLoading || !data) return <Loading />;
  const cap = data.capacity;
  const ex = data.experience;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Capacity utilisation</h3>
          </CardHeader>
          <CardBody>
            <div className="flex items-baseline gap-2">
              <span className="font-mono-num text-3xl font-bold text-[var(--color-text-primary)]">{pct(cap.utilisation_pct)}</span>
              <span className="text-sm text-[var(--color-text-secondary)]">{cap.booked} of {cap.total} slots booked</span>
            </div>
            <div className="mt-3 h-2.5 rounded-full bg-black/[0.08]">
              <div className="h-2.5 rounded-full bg-[var(--color-primary)]" style={{ width: `${Math.min(cap.utilisation_pct ?? 0, 100)}%` }} />
            </div>
            <div className="mt-4 grid grid-cols-3 gap-3">
              <MiniStat label="Available" value={cap.available} />
              <MiniStat label="Peak slot" value={<span className="text-sm">{cap.peak_slot || "—"}</span>} />
              <MiniStat label="Quietest slot" value={<span className="text-sm">{cap.lowest_slot || "—"}</span>} />
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Service quality</h3>
          </CardHeader>
          <CardBody className="space-y-2">
            <QualityRow ok={(data.on_time_arrival_pct ?? 100) >= 90} text={`On-time arrival: ${pct(data.on_time_arrival_pct)}`} />
            <QualityRow ok={(data.cancellation_rate ?? 0) <= 10} text={`Cancellation rate: ${pct(data.cancellation_rate)}`} />
            <QualityRow ok={data.captain_cancellations === 0} text={`Captain cancellations: ${data.captain_cancellations}`} />
            <QualityRow ok={ex.unresolved_complaints === 0} text={`Unresolved complaints: ${ex.unresolved_complaints}`} />
            <div className="grid grid-cols-2 gap-3 pt-2">
              <MiniStat label="Avg service time" value={num(data.avg_service_minutes, " min")} />
              <MiniStat label="Avg travel time" value={num(data.avg_travel_minutes, " min")} />
            </div>
          </CardBody>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Ratings</h3>
          </CardHeader>
          <CardBody>
            <div className="mb-4 grid grid-cols-3 gap-3">
              <MiniStat label="Avg rating" value={ex.avg_rating ? `${ex.avg_rating}★` : "—"} />
              <MiniStat label="5-star share" value={pct(ex.five_star_pct)} />
              <MiniStat label="Reviews collected" value={pct(ex.review_collection_pct)} tip="Reviews ÷ completed washes" />
            </div>
            <RatingBars distribution={ex.rating_distribution} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Complaints</h3>
          </CardHeader>
          <CardBody>
            <div className="mb-4 grid grid-cols-3 gap-3">
              <MiniStat label="Complaints" value={ex.complaints} />
              <MiniStat label="Complaint rate" value={pct(ex.complaint_rate_pct)} />
              <MiniStat label="Avg resolution" value={ex.avg_resolution_hours == null ? "—" : `${ex.avg_resolution_hours} h`} />
            </div>
            {ex.complaint_categories.length > 0 ? (
              <HBars rows={ex.complaint_categories.map((c) => ({ label: c.category.replace(/_/g, " "), value: c.count }))} />
            ) : (
              <p className="py-4 text-center text-sm text-[var(--color-text-secondary)]">No complaints in this period. 🎉</p>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function QualityRow({ ok, text }: { ok: boolean; text: string }) {
  return (
    <p className={`flex items-center gap-2 text-sm ${ok ? "text-[var(--color-text-primary)]" : "text-amber-700"}`}>
      <span>{ok ? "✓" : "⚠"}</span> {text}
    </p>
  );
}

/* ------------------------------------------------------------------ */
/* Areas                                                               */
/* ------------------------------------------------------------------ */
type AreasData = { areas: { area: string; bookings: number; revenue: number; customers: number; repeat_rate: number | null }[] };

export function AreasTab({ params }: { params: KpiPeriodParams }) {
  const { data, isLoading } = useSection<AreasData>("areas", params);
  if (isLoading || !data) return <Loading />;
  return (
    <Card>
      <CardHeader>
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Area performance</h3>
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">Where to add marketing and captain density next.</p>
        </div>
      </CardHeader>
      <CardBody className="overflow-x-auto">
        {data.areas.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--color-text-secondary)]">No bookings in this period.</p>
        ) : (
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="text-xs text-[var(--color-text-secondary)]">
              <tr>
                <th className="py-2 pr-3 font-medium">Area</th>
                <th className="py-2 pr-3 font-medium">Bookings</th>
                <th className="py-2 pr-3 font-medium">Revenue</th>
                <th className="py-2 pr-3 font-medium">Customers</th>
                <th className="py-2 font-medium">Repeat rate</th>
              </tr>
            </thead>
            <tbody className="text-[var(--color-text-primary)]">
              {data.areas.map((a) => (
                <tr key={a.area} className="border-t border-gray-50">
                  <td className="py-2.5 pr-3 font-medium">{a.area}</td>
                  <td className="py-2.5 pr-3 font-mono-num">{a.bookings}</td>
                  <td className="py-2.5 pr-3 font-mono-num">{formatINR(a.revenue)}</td>
                  <td className="py-2.5 pr-3 font-mono-num">{a.customers}</td>
                  <td className="py-2.5 font-mono-num">{a.repeat_rate == null ? "—" : `${a.repeat_rate}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardBody>
    </Card>
  );
}
