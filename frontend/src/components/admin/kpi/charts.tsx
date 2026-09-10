/**
 * Lightweight SVG/CSS chart primitives for the KPI dashboard.
 *
 * Deliberately hand-rolled instead of pulling in a chart library: the
 * existing admin design language is minimal (cards, CSS-variable colors,
 * no animation noise), the data volumes are tiny, and these stay pixel-
 * consistent with the rest of the panel.
 */
import { type ReactNode } from "react";
import { ArrowDownRight, ArrowUpRight, Info, Minus } from "lucide-react";

/** Muted, consistent series palette derived from the brand primary. */
export const SERIES_OPACITIES = [1, 0.75, 0.55, 0.4, 0.28, 0.18];

export function formatINR(v: number | null | undefined): string {
  if (v == null) return "—";
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

export function DeltaPill({ current, previous, invert = false }: { current: number | null | undefined; previous: number | null | undefined; invert?: boolean }) {
  if (current == null || previous == null || previous === 0) {
    return <span className="inline-flex items-center gap-0.5 text-xs font-medium text-[var(--color-text-secondary)]"><Minus className="h-3 w-3" />—</span>;
  }
  const pct = ((current - previous) / Math.abs(previous)) * 100;
  const up = pct >= 0;
  const good = invert ? !up : up;
  const tone = Math.abs(pct) < 0.05 ? "text-[var(--color-text-secondary)]" : good ? "text-[var(--color-success)]" : "text-[var(--color-error)]";
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${tone}`}>
      <Icon className="h-3 w-3" />
      {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

export function InfoTip({ text }: { text: string }) {
  return (
    <span title={text} className="inline-flex cursor-help text-[var(--color-text-secondary)] opacity-50 hover:opacity-100">
      <Info className="h-3.5 w-3.5" />
    </span>
  );
}

/** Small "Target 5.0 · gap -0.4" chip under a KPI value. */
export function TargetChip({ actual, target, unit = "", higherIsBetter = true }: { actual: number | null | undefined; target: number | null | undefined; unit?: string; higherIsBetter?: boolean }) {
  if (actual == null || target == null) return null;
  const gap = actual - target;
  const met = higherIsBetter ? gap >= 0 : gap <= 0;
  return (
    <span className={`mt-1 inline-block rounded-full px-2 py-0.5 text-[10px] font-medium ${met ? "bg-green-50 text-[var(--color-success)]" : "bg-amber-50 text-amber-700"}`}>
      Target {target}{unit} · {gap >= 0 ? "+" : ""}{Math.round(gap * 10) / 10}{unit}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Revenue + bookings trend                                            */
/* ------------------------------------------------------------------ */
export function TrendChart({ data }: { data: { date: string; bookings: number; revenue: number }[] }) {
  if (!data.length) return <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">No data in this period.</p>;
  const W = 640, H = 180, PAD = 8;
  const maxRev = Math.max(...data.map((d) => d.revenue), 1);
  const maxBk = Math.max(...data.map((d) => d.bookings), 1);
  const step = (W - PAD * 2) / Math.max(data.length - 1, 1);
  const x = (i: number) => PAD + i * step;
  const yRev = (v: number) => H - PAD - (v / maxRev) * (H - PAD * 2);
  const yBk = (v: number) => H - PAD - (v / maxBk) * (H - PAD * 2);
  const revPath = data.map((d, i) => `${i === 0 ? "M" : "L"}${x(i)},${yRev(d.revenue)}`).join(" ");
  const area = `${revPath} L${x(data.length - 1)},${H - PAD} L${x(0)},${H - PAD} Z`;
  const labelEvery = Math.ceil(data.length / 7);
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H + 18}`} className="w-full">
        <path d={area} fill="var(--color-primary)" opacity={0.08} />
        <path d={revPath} fill="none" stroke="var(--color-primary)" strokeWidth={2} strokeLinejoin="round" />
        {data.map((d, i) => (
          <g key={d.date}>
            <rect x={x(i) - 2} y={yBk(d.bookings)} width={4} height={H - PAD - yBk(d.bookings)} rx={1.5} fill="var(--color-secondary)" opacity={0.55}>
              <title>{`${d.date} · ${d.bookings} bookings · ${formatINR(d.revenue)}`}</title>
            </rect>
            <circle cx={x(i)} cy={yRev(d.revenue)} r={2.5} fill="var(--color-primary)">
              <title>{`${d.date} · ${formatINR(d.revenue)}`}</title>
            </circle>
            {i % labelEvery === 0 && (
              <text x={x(i)} y={H + 12} textAnchor="middle" className="fill-[var(--color-text-secondary)]" fontSize={9}>
                {d.date.slice(5)}
              </text>
            )}
          </g>
        ))}
      </svg>
      <div className="mt-1 flex items-center gap-4 text-xs text-[var(--color-text-secondary)]">
        <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-4 rounded bg-[var(--color-primary)]" /> Revenue</span>
        <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-1.5 rounded-sm bg-[var(--color-secondary)] opacity-60" /> Bookings</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Donut + horizontal bars                                             */
/* ------------------------------------------------------------------ */
export function Donut({ items, valueLabel }: { items: { name: string; value: number; sub?: string }[]; valueLabel?: (v: number) => string }) {
  const total = items.reduce((a, b) => a + b.value, 0);
  if (!total) return <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">No data in this period.</p>;
  const R = 40, C = 2 * Math.PI * R;
  let acc = 0;
  return (
    <div className="flex flex-wrap items-center gap-6">
      <svg viewBox="0 0 100 100" className="h-32 w-32 shrink-0 -rotate-90">
        {items.map((it, i) => {
          const frac = it.value / total;
          const dash = `${frac * C} ${C}`;
          const offset = -acc * C;
          acc += frac;
          return (
            <circle key={it.name} cx={50} cy={50} r={R} fill="none" stroke="var(--color-primary)" strokeOpacity={SERIES_OPACITIES[i % SERIES_OPACITIES.length]} strokeWidth={14} strokeDasharray={dash} strokeDashoffset={offset}>
              <title>{`${it.name}: ${Math.round(frac * 100)}%`}</title>
            </circle>
          );
        })}
      </svg>
      <ul className="min-w-0 flex-1 space-y-1.5">
        {items.map((it, i) => (
          <li key={it.name} className="flex items-center gap-2 text-sm">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm bg-[var(--color-primary)]" style={{ opacity: SERIES_OPACITIES[i % SERIES_OPACITIES.length] }} />
            <span className="min-w-0 flex-1 truncate text-[var(--color-text-primary)]">{it.name}</span>
            <span className="font-mono-num text-xs font-semibold text-[var(--color-text-primary)]">{valueLabel ? valueLabel(it.value) : it.value}</span>
            <span className="w-10 text-right font-mono-num text-xs text-[var(--color-text-secondary)]">{Math.round((it.value / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HBars({ rows, valueLabel }: { rows: { label: string; value: number; sub?: string }[]; valueLabel?: (v: number) => string }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  if (!rows.length) return <p className="py-6 text-center text-sm text-[var(--color-text-secondary)]">No data in this period.</p>;
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.label}>
          <div className="mb-0.5 flex items-baseline justify-between gap-2 text-sm">
            <span className="min-w-0 truncate text-[var(--color-text-primary)]">{r.label}</span>
            <span className="shrink-0 font-mono-num text-xs font-semibold text-[var(--color-text-primary)]">
              {valueLabel ? valueLabel(r.value) : r.value}
              {r.sub && <span className="ml-1.5 font-normal text-[var(--color-text-secondary)]">{r.sub}</span>}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-black/[0.08]">
            <div className="h-1.5 rounded-full bg-[var(--color-primary)]" style={{ width: `${(r.value / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function RatingBars({ distribution }: { distribution: Record<string, number> }) {
  const total = Object.values(distribution).reduce((a, b) => a + b, 0);
  return (
    <ul className="space-y-1.5">
      {["5", "4", "3", "2", "1"].map((star) => {
        const count = distribution[star] || 0;
        return (
          <li key={star} className="flex items-center gap-2 text-xs">
            <span className="w-6 shrink-0 text-[var(--color-text-secondary)]">{star}★</span>
            <div className="h-2 flex-1 rounded-full bg-black/[0.08]">
              <div className={`h-2 rounded-full ${star >= "4" ? "bg-[var(--color-success)]" : star === "3" ? "bg-amber-400" : "bg-[var(--color-error)]"}`} style={{ width: total ? `${(count / total) * 100}%` : 0 }} />
            </div>
            <span className="w-6 shrink-0 text-right font-mono-num text-[var(--color-text-secondary)]">{count}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function Funnel({ steps }: { steps: { label: string; value: number }[] }) {
  const max = Math.max(...steps.map((s) => s.value), 1);
  return (
    <ol className="space-y-1.5">
      {steps.map((s, i) => {
        const prev = i > 0 ? steps[i - 1].value : null;
        const conv = prev ? Math.round((s.value / prev) * 100) : null;
        return (
          <li key={s.label} className="flex items-center gap-3">
            <div className="h-8 min-w-[3rem] rounded-lg bg-[var(--color-primary)] px-2 text-right leading-8" style={{ width: `${Math.max((s.value / max) * 100, 12)}%`, opacity: SERIES_OPACITIES[Math.min(i, SERIES_OPACITIES.length - 1)] }}>
              <span className="font-mono-num text-xs font-bold text-white">{s.value}</span>
            </div>
            <span className="text-xs text-[var(--color-text-primary)]">
              {s.label}
              {conv != null && <span className="ml-1.5 text-[var(--color-text-secondary)]">({conv}%)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ */
/* Small building blocks                                               */
/* ------------------------------------------------------------------ */
export function MiniStat({ label, value, tip, children }: { label: string; value: ReactNode; tip?: string; children?: ReactNode }) {
  // Same console language as ui/StatCard (the blueprint tile), one size
  // down: hairline card, sentence-case grey label, big mono number.
  return (
    <div className="rounded-xl border border-[#F3E5B5] bg-white p-4">
      <p className="flex items-center gap-1 text-xs font-medium text-gray-500">
        {label} {tip && <InfoTip text={tip} />}
      </p>
      <p className="font-mono-num mt-1.5 text-xl font-bold text-black">{value}</p>
      {children}
    </div>
  );
}

export function SectionCaption({ children }: { children: ReactNode }) {
  return <p className="mb-3 text-sm font-semibold text-black">{children}</p>;
}
