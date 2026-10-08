/**
 * Trends & breakdowns — the admin dashboard's interactive charts.
 *
 * One filter row scopes everything in this panel (period presets + custom
 * range, center, service, car type, channel, bucket size); the server
 * computes every number (GET /analytics/kpis-explorer, cached a minute) with
 * the same definitions as the KPI tiles. Every bar and every point drills
 * into the real records behind it: bookings (created in the bucket),
 * completed washes (revenue, by completion date) or plan payments.
 */
import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { RotateCcw, SlidersHorizontal, Table2, BarChart3 } from "lucide-react";
import { kpiApi, type KpiExplorerBucket, type KpiExplorerFilters } from "../../../api/admin";
import { bookingApi } from "../../../api/booking";
import { subscriptionApi, type PlanPurchaseRow } from "../../../api/engagement";
import { ErrorState, Select, StatusBadge } from "../../ui";
import { DatePicker } from "../../ui/DatePicker";
import { KpiListModal } from "./KpiListModal";
import { BookingDetailDrawer } from "../../shared/BookingDetailDrawer";
import { CustomerDetailDrawer } from "../../shared/CustomerDetailDrawer";
import { DeltaPill } from "./charts";
import { BarList, ColumnChart, LineChart, Refetching, compactRupees, formatRupees, type SeriesPoint } from "./vizCharts";
import { format, formatSlot } from "../../../lib/date";
import { carAndService, toTitle } from "../../../lib/titleCase";
import type { Booking } from "../../../types";

const PRESETS = [
  { key: "7d", label: "7 Days" },
  { key: "30d", label: "30 Days" },
  { key: "this_month", label: "This Month" },
  { key: "last_month", label: "Last Month" },
] as const;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2026-09-28" -> {d: 28, m: 8, y: 2026, wd: 1} without any timezone drift. */
function parts(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return { y, m: m - 1, d, wd };
}
function dayLabel(iso: string) {
  const p = parts(iso);
  return `${p.d} ${MONTHS[p.m]}`;
}

function bucketPoint(b: KpiExplorerBucket, granularity: string, value: number, rows: SeriesPoint["rows"]): SeriesPoint {
  const s = parts(b.start);
  if (granularity === "month") {
    return { key: b.key, label: MONTHS[s.m], title: `${MONTHS[s.m]} ${s.y}${b.end !== b.start ? ` (${dayLabel(b.start)} – ${dayLabel(b.end)})` : ""}`, value, rows };
  }
  if (granularity === "week") {
    return { key: b.key, label: dayLabel(b.start), title: `Week Of ${dayLabel(b.start)} – ${dayLabel(b.end)}`, value, rows };
  }
  return { key: b.key, label: dayLabel(b.start), title: `${WEEKDAYS[s.wd]}, ${dayLabel(b.start)} ${s.y}`, value, rows };
}

type Drill =
  | { kind: "bookings" | "completed"; title: string; start: string; end: string; service_id?: string; vehicle_type?: string; source?: string; service_center_id?: string }
  | { kind: "plans"; title: string; start: string; end: string; plan_id?: string };

type Metric = "bookings" | "revenue";

function MetricToggle({ value, onChange }: { value: Metric; onChange: (m: Metric) => void }) {
  return (
    <div role="tablist" aria-label="Measure" className="inline-flex rounded-[10px] bg-[#EEF3FA] p-0.5">
      {(["bookings", "revenue"] as const).map((m) => (
        <button
          key={m}
          type="button"
          role="tab"
          aria-selected={value === m}
          onClick={() => onChange(m)}
          className={`rounded-[8px] px-2.5 py-1 text-xs font-semibold transition-colors ${value === m ? "bg-white text-[#0E1A33] shadow-[0_1px_2px_rgba(14,26,51,0.12)]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
        >
          {m === "bookings" ? "Bookings" : "Revenue"}
        </button>
      ))}
    </div>
  );
}

function ChartCard({ title, subtitle, actions, children }: { title: string; subtitle?: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded-[14px] border border-[#E4E9F1] bg-white">
      <header className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4 sm:px-5">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-[#0E1A33]">{title}</h3>
          {subtitle && <p className="mt-0.5 text-xs text-[#5F6878]">{subtitle}</p>}
        </div>
        {actions}
      </header>
      <div className="px-3 pb-4 pt-3 sm:px-4">{children}</div>
    </section>
  );
}

export function KpiExplorer() {
  const [preset, setPreset] = useState<string>("30d");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [showCustom, setShowCustom] = useState(false);
  const [center, setCenter] = useState("");
  const [service, setService] = useState("");
  const [vehicleType, setVehicleType] = useState("");
  const [source, setSource] = useState("");
  const [granularity, setGranularity] = useState<NonNullable<KpiExplorerFilters["granularity"]>>("auto");
  const [serviceMetric, setServiceMetric] = useState<Metric>("bookings");
  const [typeMetric, setTypeMetric] = useState<Metric>("bookings");
  const [asTable, setAsTable] = useState(false);
  const [drill, setDrill] = useState<Drill | null>(null);
  const [openBooking, setOpenBooking] = useState<Booking | null>(null);
  const [openCustomerId, setOpenCustomerId] = useState<string | null>(null);

  const filters: KpiExplorerFilters = useMemo(
    () => ({
      ...(preset === "custom" && custom.start && custom.end ? { start: custom.start, end: custom.end } : { period: preset === "custom" ? "30d" : preset }),
      service_center_id: center || undefined,
      service_id: service || undefined,
      vehicle_type: vehicleType || undefined,
      source: source || undefined,
      granularity,
    }),
    [preset, custom, center, service, vehicleType, source, granularity],
  );

  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["kpi-explorer", filters],
    queryFn: () => kpiApi.explorer(filters),
    placeholderData: keepPreviousData,
  });

  const dims = { service_center_id: center || undefined, service_id: service || undefined, vehicle_type: vehicleType || undefined, source: source || undefined };
  const anyFilter = !!(center || service || vehicleType || source || granularity !== "auto");

  const opts = data?.options;
  const gran = data?.range.granularity || "day";
  const series = data?.series || [];
  const bookingPoints = series.map((b) =>
    bucketPoint(b, gran, b.bookings, [
      { label: "Completed", value: b.completed },
      { label: "Cancelled", value: b.cancelled },
    ]),
  );
  const revenuePoints = series.map((b) =>
    bucketPoint(b, gran, b.revenue, [
      { label: "Washes Completed", value: b.completed },
      { label: "Avg Per Wash", value: b.completed ? formatRupees(b.revenue / b.completed) : "—" },
    ]),
  );
  const planPoints = series.map((b) => bucketPoint(b, gran, b.plans_sold, [{ label: "Plan Revenue", value: formatRupees(b.plan_revenue) }]));
  const bucketByKey = new Map(series.map((b) => [b.key, b]));

  const t = data?.totals;
  const p = data?.previous;
  const rangeLabel = data ? (data.range.start === data.range.end ? dayLabel(data.range.start) : `${dayLabel(data.range.start)} – ${dayLabel(data.range.end)} ${parts(data.range.end).y}`) : "";
  const multiCenter = (opts?.centers.length || 0) > 1;

  return (
    <section aria-labelledby="kpi-trends-title" className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="kpi-trends-title" className="text-lg font-bold text-[#0E1A33]">Trends</h2>
          <p className="text-xs text-[#5F6878]">
            {rangeLabel ? `${rangeLabel} · ` : ""}hover any bar for detail, click it to see the bookings behind it.
          </p>
        </div>
      </div>

      {/* One filter row — it scopes every chart and number in this panel. */}
      <div className="flex flex-wrap items-end gap-2 rounded-[14px] border border-[#E4E9F1] bg-[#F7F9FC] p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {PRESETS.map((pr) => (
            <button
              key={pr.key}
              type="button"
              onClick={() => {
                setPreset(pr.key);
                setShowCustom(false);
              }}
              className={`h-9 rounded-[10px] px-3 text-xs font-semibold transition-colors ${preset === pr.key ? "bg-[#0A66F0] text-white" : "border border-[#E4E9F1] bg-white text-[#5F6878] hover:border-[#C9D6EA] hover:text-[#0E1A33]"}`}
            >
              {pr.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setShowCustom((v) => !v)}
            aria-expanded={showCustom}
            className={`inline-flex h-9 items-center gap-1 rounded-[10px] px-3 text-xs font-semibold transition-colors ${preset === "custom" ? "bg-[#0A66F0] text-white" : "border border-[#E4E9F1] bg-white text-[#5F6878] hover:border-[#C9D6EA] hover:text-[#0E1A33]"}`}
          >
            <SlidersHorizontal className="h-3 w-3" /> {preset === "custom" && custom.start ? `${dayLabel(custom.start)} – ${dayLabel(custom.end)}` : "Custom"}
          </button>
        </div>
        <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-1 sm:flex-wrap sm:justify-end">
          {multiCenter && (
            <Select compact aria-label="Service center" wrapperClassName="sm:w-44" value={center} onChange={(e) => setCenter(e.target.value)}>
              <option value="">All Centers</option>
              {opts!.centers.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          )}
          <Select compact aria-label="Service" wrapperClassName="sm:w-44" value={service} onChange={(e) => setService(e.target.value)}>
            <option value="">All Services</option>
            {(opts?.services || []).map((s) => (
              <option key={s.id} value={s.id}>{toTitle(s.name)}</option>
            ))}
          </Select>
          <Select compact aria-label="Car type" wrapperClassName="sm:w-36" value={vehicleType} onChange={(e) => setVehicleType(e.target.value)}>
            <option value="">All Car Types</option>
            {(opts?.vehicle_types || []).map((v) => (
              <option key={v.id} value={v.id}>{toTitle(v.name)}</option>
            ))}
          </Select>
          <Select compact aria-label="Channel" wrapperClassName="sm:w-44" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All Channels</option>
            {(opts?.sources || []).map((s) => (
              <option key={s.key} value={s.key}>{toTitle(s.name)}</option>
            ))}
          </Select>
          <Select compact aria-label="Group by" wrapperClassName="sm:w-32" value={granularity} onChange={(e) => setGranularity(e.target.value as typeof granularity)}>
            <option value="auto">Auto</option>
            <option value="day">By Day</option>
            <option value="week">By Week</option>
            <option value="month">By Month</option>
          </Select>
          {anyFilter && (
            <button
              type="button"
              onClick={() => {
                setCenter("");
                setService("");
                setVehicleType("");
                setSource("");
                setGranularity("auto");
              }}
              className="inline-flex h-10 items-center justify-center gap-1 rounded-[10px] px-2.5 text-xs font-semibold text-[#0A66F0] hover:bg-[#E8F0FE]"
            >
              <RotateCcw className="h-3 w-3" /> Reset
            </button>
          )}
        </div>
        {showCustom && (
          <div className="flex w-full flex-wrap items-end gap-2 border-t border-[#E4E9F1] pt-3">
            <div className="w-40"><DatePicker label="From" value={custom.start} onChange={(v) => setCustom((c) => ({ ...c, start: v }))} /></div>
            <div className="w-40"><DatePicker label="To" value={custom.end} min={custom.start || undefined} onChange={(v) => setCustom((c) => ({ ...c, end: v }))} /></div>
            <button
              type="button"
              disabled={!custom.start || !custom.end || custom.end < custom.start}
              onClick={() => {
                setPreset("custom");
                setShowCustom(false);
              }}
              className="h-11 rounded-[10px] bg-[#0A66F0] px-4 text-sm font-semibold text-white hover:bg-[#0857D0] disabled:opacity-40"
            >
              Apply
            </button>
          </div>
        )}
      </div>

      {isError && !data ? (
        <ErrorState message="Couldn't load trends." onRetry={() => void refetch()} busy={isFetching} />
      ) : isLoading && !data ? (
        <div className="h-[420px] animate-pulse rounded-[14px] bg-[#EEF3FA]" />
      ) : data && t && p ? (
        <Refetching busy={isFetching}>
          <div className="space-y-4">
            {/* Totals for the slice, each against the previous equal-length period. */}
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[14px] border border-[#E4E9F1] bg-[#E4E9F1] sm:grid-cols-3 lg:grid-cols-6">
              {[
                { label: "Bookings", value: t.bookings.toLocaleString("en-IN"), cur: t.bookings, prev: p.bookings },
                { label: "Completed", value: t.completed.toLocaleString("en-IN"), cur: t.completed, prev: p.completed },
                { label: "Booking Revenue", value: formatRupees(t.revenue), cur: t.revenue, prev: p.revenue },
                { label: "Avg Per Wash", value: formatRupees(t.aov), cur: t.aov, prev: p.aov },
                { label: "Plans Sold", value: t.plans_sold.toLocaleString("en-IN"), cur: t.plans_sold, prev: p.plans_sold },
                { label: "Plan Revenue", value: formatRupees(t.plan_revenue), cur: t.plan_revenue, prev: p.plan_revenue },
              ].map((k) => (
                <div key={k.label} className="bg-white px-4 py-3">
                  <dt className="text-xs text-[#5F6878]">{k.label}</dt>
                  <dd className="mt-1 flex flex-wrap items-baseline gap-x-2">
                    <span className="font-mono-num text-lg font-bold text-[#0E1A33]">{k.value}</span>
                    <DeltaPill current={k.cur} previous={k.prev} />
                  </dd>
                </div>
              ))}
            </dl>

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <ChartCard
                title="Bookings Over Time"
                subtitle={`Bookings created, ${gran === "day" ? "per day" : gran === "week" ? "per week" : "per month"}`}
                actions={
                  <button
                    type="button"
                    onClick={() => setAsTable((v) => !v)}
                    className="inline-flex items-center gap-1 rounded-[8px] px-2 py-1 text-xs font-semibold text-[#5F6878] hover:bg-[#EEF3FA] hover:text-[#0E1A33]"
                  >
                    {asTable ? <BarChart3 className="h-3.5 w-3.5" /> : <Table2 className="h-3.5 w-3.5" />} {asTable ? "Chart" : "Table"}
                  </button>
                }
              >
                {asTable ? (
                  <div className="max-h-[260px] overflow-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="sticky top-0 bg-white text-[#5F6878]">
                        <tr>
                          <th className="py-1.5 pr-2 font-medium">Period</th>
                          <th className="py-1.5 pr-2 text-right font-medium">Bookings</th>
                          <th className="py-1.5 pr-2 text-right font-medium">Completed</th>
                          <th className="py-1.5 pr-2 text-right font-medium">Revenue</th>
                          <th className="py-1.5 text-right font-medium">Plans</th>
                        </tr>
                      </thead>
                      <tbody className="font-mono-num text-[#0E1A33]">
                        {series.map((b, i) => (
                          <tr key={b.key} className="border-t border-[#EEF2F7]">
                            <td className="py-1.5 pr-2 font-sans">{bookingPoints[i].title}</td>
                            <td className="py-1.5 pr-2 text-right">{b.bookings}</td>
                            <td className="py-1.5 pr-2 text-right">{b.completed}</td>
                            <td className="py-1.5 pr-2 text-right">{formatRupees(b.revenue)}</td>
                            <td className="py-1.5 text-right">{b.plans_sold}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <ColumnChart
                    ariaLabel="Bookings over time"
                    points={bookingPoints}
                    onSelect={(pt) => {
                      const b = bucketByKey.get(pt.key)!;
                      setDrill({ kind: "bookings", title: `Bookings · ${pt.title}`, start: b.start, end: b.end, ...dims });
                    }}
                  />
                )}
              </ChartCard>

              <ChartCard title="Revenue Over Time" subtitle="Completed-wash revenue, by the day each wash was finished">
                <LineChart
                  ariaLabel="Revenue over time"
                  points={revenuePoints}
                  format={formatRupees}
                  axisFormat={compactRupees}
                  selectHint="Click to see these washes"
                  onSelect={(pt) => {
                    const b = bucketByKey.get(pt.key)!;
                    setDrill({ kind: "completed", title: `Completed Washes · ${pt.title}`, start: b.start, end: b.end, ...dims });
                  }}
                />
              </ChartCard>

              <ChartCard
                title="By Service"
                subtitle={serviceMetric === "bookings" ? "Bookings that include each service" : "Revenue, a multi-service booking split evenly"}
                actions={<MetricToggle value={serviceMetric} onChange={setServiceMetric} />}
              >
                <BarList
                  rows={data.by_service.map((s) => ({
                    key: s.id,
                    label: toTitle(s.name),
                    value: serviceMetric === "bookings" ? s.bookings : s.revenue,
                    rows: [
                      { label: "Bookings", value: s.bookings },
                      { label: "Completed", value: s.completed },
                      { label: "Revenue", value: formatRupees(s.revenue) },
                    ],
                  }))}
                  format={serviceMetric === "bookings" ? (v) => v.toLocaleString("en-IN") : formatRupees}
                  onSelect={(r) =>
                    setDrill({
                      kind: serviceMetric === "bookings" ? "bookings" : "completed",
                      title: `${r.label} · ${rangeLabel}`,
                      start: data.range.start,
                      end: data.range.end,
                      ...dims,
                      service_id: r.key,
                    })
                  }
                />
              </ChartCard>

              <ChartCard
                title="By Car Type"
                subtitle={typeMetric === "bookings" ? "Bookings per car type" : "Completed-wash revenue per car type"}
                actions={<MetricToggle value={typeMetric} onChange={setTypeMetric} />}
              >
                <BarList
                  rows={data.by_vehicle_type.map((v) => ({
                    key: v.id || "unknown",
                    label: toTitle(v.name),
                    value: typeMetric === "bookings" ? v.bookings : v.revenue,
                    rows: [
                      { label: "Bookings", value: v.bookings },
                      { label: "Revenue", value: formatRupees(v.revenue) },
                    ],
                  }))}
                  format={typeMetric === "bookings" ? (v) => v.toLocaleString("en-IN") : formatRupees}
                  onSelect={(r) =>
                    r.key !== "unknown" &&
                    setDrill({
                      kind: typeMetric === "bookings" ? "bookings" : "completed",
                      title: `${r.label} · ${rangeLabel}`,
                      start: data.range.start,
                      end: data.range.end,
                      ...dims,
                      vehicle_type: r.key,
                    })
                  }
                />
              </ChartCard>

              <ChartCard title="Plans Sold" subtitle={`Paid plan purchases${source ? " (channel filter doesn't apply to plans)" : ""}`}>
                <ColumnChart
                  ariaLabel="Plans sold over time"
                  points={planPoints}
                  onSelect={(pt) => {
                    const b = bucketByKey.get(pt.key)!;
                    setDrill({ kind: "plans", title: `Plans Sold · ${pt.title}`, start: b.start, end: b.end });
                  }}
                  height={180}
                />
                {data.by_plan.length > 0 && (
                  <div className="mt-3 border-t border-[#EEF2F7] pt-3">
                    <BarList
                      rows={data.by_plan.map((pl) => ({
                        key: pl.id || "unknown",
                        label: toTitle(pl.name),
                        value: pl.sold,
                        rows: [{ label: "Revenue", value: formatRupees(pl.revenue) }],
                      }))}
                      onSelect={(r) =>
                        r.key !== "unknown" &&
                        setDrill({ kind: "plans", title: `${r.label} · ${rangeLabel}`, start: data.range.start, end: data.range.end, plan_id: r.key })
                      }
                      limit={5}
                    />
                  </div>
                )}
              </ChartCard>

              <ChartCard title="By Channel" subtitle="Where bookings came from">
                <BarList
                  rows={data.by_source.map((s) => ({
                    key: s.key,
                    label: toTitle(s.name),
                    value: s.bookings,
                    rows: [{ label: "Revenue", value: formatRupees(s.revenue) }],
                  }))}
                  format={(v) => v.toLocaleString("en-IN")}
                  onSelect={(r) =>
                    setDrill({ kind: "bookings", title: `${r.label} · ${rangeLabel}`, start: data.range.start, end: data.range.end, ...dims, source: r.key })
                  }
                />
                {multiCenter && !center && (
                  <div className="mt-3 border-t border-[#EEF2F7] pt-3">
                    <p className="mb-1.5 px-2 text-xs font-medium text-[#5F6878]">By Service Center</p>
                    <BarList
                      rows={data.by_center.map((c) => ({
                        key: c.id || "none",
                        label: c.name,
                        value: c.bookings,
                        rows: [{ label: "Revenue", value: formatRupees(c.revenue) }],
                      }))}
                      format={(v) => v.toLocaleString("en-IN")}
                      onSelect={(r) =>
                        r.key !== "none" &&
                        setDrill({ kind: "bookings", title: `${r.label} · ${rangeLabel}`, start: data.range.start, end: data.range.end, ...dims, service_center_id: r.key })
                      }
                    />
                  </div>
                )}
              </ChartCard>
            </div>
          </div>
        </Refetching>
      ) : null}

      <ExplorerDrill drill={drill} onClose={() => setDrill(null)} onBooking={setOpenBooking} onCustomer={setOpenCustomerId} filters={filters} />
      <BookingDetailDrawer booking={openBooking} onClose={() => setOpenBooking(null)} />
      <CustomerDetailDrawer customerId={openCustomerId} onClose={() => setOpenCustomerId(null)} />
    </section>
  );
}

function ExplorerDrill({
  drill,
  onClose,
  onBooking,
  onCustomer,
  filters,
}: {
  drill: Drill | null;
  onClose: () => void;
  onBooking: (b: Booking) => void;
  onCustomer: (id: string) => void;
  filters: KpiExplorerFilters;
}) {
  const isPlans = drill?.kind === "plans";
  return (
    <>
      <KpiListModal<Booking>
        open={!!drill && !isPlans}
        onClose={onClose}
        title={drill?.title || "Bookings"}
        queryKey={["kpi-explorer-drill", drill]}
        fetchFn={(page) => {
          const d = drill as Extract<Drill, { kind: "bookings" | "completed" }>;
          return bookingApi.all({
            start: d.start,
            end: d.end,
            page,
            page_size: 50,
            date_field: d.kind === "completed" ? "completed" : "created",
            service_center_id: d.service_center_id,
            service_id: d.service_id,
            vehicle_type: d.vehicle_type,
            source: d.source,
          });
        }}
        getRowKey={(b) => b.id}
        renderRow={(b) => (
          <button type="button" onClick={() => onBooking(b)} className="flex w-full items-center justify-between gap-3 text-left">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[#0E1A33]">{toTitle(b.combo_name || b.service_names?.join(", ")) || "Booking"} · {b.customer_name || "—"}</p>
              <p className="truncate text-xs text-[#5F6878]">{[b.booking_number, toTitle(b.vehicle_type_name || b.vehicle_label), format(b.scheduled_date), formatSlot(b.scheduled_slot)].filter(Boolean).join(" · ")}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="font-mono-num text-sm text-[#0E1A33]">{formatRupees(b.total_amount)}</span>
              <StatusBadge status={b.status} />
            </div>
          </button>
        )}
      />
      <KpiListModal<PlanPurchaseRow>
        open={!!drill && isPlans}
        onClose={onClose}
        title={drill?.title || "Plans Sold"}
        queryKey={["kpi-explorer-drill-plans", drill, filters.service_center_id, filters.service_id, filters.vehicle_type]}
        fetchFn={(page) => {
          const d = drill as Extract<Drill, { kind: "plans" }>;
          return subscriptionApi.planPurchases({
            start: d.start,
            end: d.end,
            page,
            page_size: 50,
            plan_id: d.plan_id,
            service_center_id: filters.service_center_id,
            service_id: filters.service_id,
            vehicle_type: filters.vehicle_type,
          });
        }}
        getRowKey={(row) => row.id}
        emptyText="No plans sold in this slice."
        renderRow={(row) => (
          <button type="button" onClick={() => row.customer_id && onCustomer(row.customer_id)} className="flex w-full items-center justify-between gap-3 text-left">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[#0E1A33]">{toTitle(row.plan_name)} · {row.customer_name}</p>
              {(row.vehicle_type_name || row.service_name) && (
                <p className="truncate text-xs text-[#0E1A33]">{carAndService(row.vehicle_type_name, row.service_name)}</p>
              )}
              <p className="text-xs text-[#5F6878]">{format(row.created_at)}{row.payment_method ? ` · ${row.payment_method}` : ""}</p>
            </div>
            <span className="font-mono-num shrink-0 text-sm text-[#0E1A33]">{formatRupees(row.amount)}</span>
          </button>
        )}
      />
    </>
  );
}

