import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, IndianRupee } from "lucide-react";
import { type CollectionsReport as Report } from "../../api/payment";
import { Card, CardBody, CardHeader, Input } from "../ui";
import { formatDateTime } from "../../lib/date";

/**
 * The money-acknowledgment ledger, shared by both oversight levels:
 * manager (one row per CAPTAIN of their center) and admin (one row per
 * CENTER, plus subscription revenue and the needs-attention queue).
 * Cash vs online vs UNCOLLECTED per row — uncollected on a completed
 * booking is the surveillance number: someone finished a wash and no
 * payment was recorded.
 */
type PresetKey = "today" | "week" | "month" | "30d" | "custom";

const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "This week" },
  { key: "month", label: "This month" },
  { key: "30d", label: "Last 30 days" },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Ranges the way a settlement conversation actually works: today, the week
 *  so far (Monday-based), the calendar month so far. */
function presetRange(key: PresetKey): { from: string; to: string } {
  const today = new Date();
  if (key === "today") return { from: iso(today), to: iso(today) };
  if (key === "week") {
    const monday = new Date(today);
    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    return { from: iso(monday), to: iso(today) };
  }
  if (key === "month") {
    return { from: iso(new Date(today.getFullYear(), today.getMonth(), 1)), to: iso(today) };
  }
  return { from: "", to: "" }; // 30d is the backend default
}

export function CollectionsReportCard({
  title,
  entityLabel,
  queryKey,
  fetcher,
}: {
  title: string;
  entityLabel: string;
  queryKey: string;
  fetcher: (params: { date_from?: string; date_to?: string }) => Promise<Report>;
}) {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [preset, setPreset] = useState<PresetKey>("30d");

  const applyPreset = (key: PresetKey) => {
    setPreset(key);
    const { from, to } = presetRange(key);
    setDateFrom(from);
    setDateTo(to);
  };

  const { data, isLoading } = useQuery({
    queryKey: [queryKey, dateFrom, dateTo],
    queryFn: () => fetcher({ date_from: dateFrom || undefined, date_to: dateTo || undefined }),
  });

  const rows = data?.rows || [];
  const t = data?.totals;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <IndianRupee className="h-4 w-4 text-[var(--color-primary)]" />
          <h2 className="font-semibold text-[var(--color-text-primary)]">{title}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Settlement happens by day, week and month — one tap each; the
              date boxes stay for anything else. */}
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => applyPreset(option.key)}
                aria-pressed={preset === option.key}
                className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                  preset === option.key ? "border-black bg-black text-white" : "border-[#E5E7EB] text-gray-600 hover:border-gray-400"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Input
              type="date"
              value={dateFrom}
              max={dateTo || undefined}
              onChange={(e) => { setDateFrom(e.target.value); setPreset("custom"); }}
              placeholder="From"
            />
            <Input
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              onChange={(e) => { setDateTo(e.target.value); setPreset("custom"); }}
              placeholder="To"
            />
          </div>
        </div>
      </CardHeader>
      <CardBody>
        <p
          className="mb-3 text-xs text-[var(--color-text-secondary)]"
          title={'Paid bookings only. "Uncollected" = completed washes with no payment recorded yet.'}
        >
          {PRESETS.find((option) => option.key === preset)?.label || "Selected range"}
        </p>
        {isLoading ? (
          <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
        ) : !rows.length ? (
          <p className="text-sm text-[var(--color-text-secondary)]">No collections in this period.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-[var(--color-text-secondary)]">
                  <th className="py-2 pr-3 font-medium">{entityLabel}</th>
                  <th className="py-2 pr-3 font-medium">Cash</th>
                  <th className="py-2 pr-3 font-medium">Online</th>
                  <th className="py-2 font-medium">Uncollected</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.captain_id || r.service_center_id || "none"} className="border-b border-gray-50 last:border-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-[var(--color-text-primary)]">{r.captain_name || r.center_name || "—"}</span>
                      {r.employee_id && <span className="ml-1.5 rounded-full bg-black px-1.5 py-0.5 font-mono-num text-[9px] font-bold text-white">{r.employee_id}</span>}
                    </td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{r.cash_amount} <span className="text-xs text-gray-400">({r.cash_count})</span></td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{r.online_amount} <span className="text-xs text-gray-400">({r.online_count})</span></td>
                    <td className={`py-2.5 font-mono-num ${r.uncollected_amount > 0 ? "font-bold text-amber-600" : "text-gray-400"}`}>
                      ₹{r.uncollected_amount} <span className="text-xs">({r.uncollected_count})</span>
                    </td>
                  </tr>
                ))}
                {t && (
                  <tr className="border-t border-gray-200 font-bold text-[var(--color-text-primary)]">
                    <td className="py-2.5 pr-3">Total</td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{t.cash_amount}</td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{t.online_amount}</td>
                    <td className={`py-2.5 font-mono-num ${t.uncollected_amount > 0 ? "text-amber-600" : ""}`}>₹{t.uncollected_amount}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {data?.subscriptions && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            Subscription revenue (online): <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.subscriptions.online_amount}</span>{" "}
            across {data.subscriptions.count} purchase{data.subscriptions.count === 1 ? "" : "s"}.
          </p>
        )}

        {!!data?.attention?.length && (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/50 p-3">
            <p className="mb-2 flex items-center gap-1.5 text-sm font-bold text-amber-800">
              <AlertTriangle className="h-4 w-4" /> Payments needing attention ({data.attention.length})
            </p>
            <div className="space-y-1.5">
              {data.attention.map((a, i) => (
                <p key={i} className="text-xs text-amber-900">
                  <span className="font-mono-num font-bold">₹{a.amount}</span>
                  {a.booking_number ? ` · ${a.booking_number}` : a.purpose === "subscription" ? " · subscription" : ""} — {a.reason}
                  {a.flagged_at ? ` (${formatDateTime(a.flagged_at)})` : ""}
                </p>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-amber-700">Money was received but couldn't be applied automatically — settle these manually (refund or fix), then they clear from Razorpay's dashboard.</p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
