/**
 * Interactive chart primitives for the admin KPI explorer — hand-rolled
 * SVG (the app ships no chart library; these stay tiny and on-theme).
 *
 * Rules they follow (dataviz method): one measure per chart and one axis —
 * never a dual axis; a single series is ONE colour (v2 blue) for every
 * mark; thin marks (columns ≤ 24px with a 4px rounded data-end, square at
 * the baseline; 2px lines with a ~10% area wash); hairline solid gridlines;
 * text always in ink tokens, never the series colour. Every mark has a
 * hover/focus tooltip (values lead, labels follow) with a hit target larger
 * than the mark, and every mark is clickable/Enter-able to drill into the
 * records behind it.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

export const SERIES = "#0A66F0";
const INK = "#0E1A33";
const MUTED = "#5F6878";
const AXIS = "#8A93A3";
const GRID = "#EEF2F7";
const BASELINE = "#C9D6EA";

export function formatRupees(v: number | null | undefined): string {
  if (v == null) return "—";
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

/** Axis-tick money: ₹950 · ₹12K · ₹1.2L · ₹3.4Cr (Indian units). */
export function compactRupees(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e7) return `₹${trim(v / 1e7)}Cr`;
  if (a >= 1e5) return `₹${trim(v / 1e5)}L`;
  if (a >= 1e3) return `₹${trim(v / 1e3)}K`;
  return `₹${Math.round(v)}`;
}
function trim(n: number) {
  return (Math.round(n * 10) / 10).toString().replace(/\.0$/, "");
}
export function compactCount(v: number): string {
  if (Math.abs(v) >= 1e5) return `${trim(v / 1e5)}L`;
  if (Math.abs(v) >= 1e3) return `${trim(v / 1e3)}K`;
  return String(Math.round(v));
}

/** 0 + 3–4 clean ticks covering max (1/2/2.5/5 × 10^n steps). */
function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
  return ticks;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export type TipRow = { label: string; value: ReactNode };

function Tooltip({ x, y, width, title, value, rows, hint }: { x: number; y: number; width: number; title: string; value: ReactNode; rows?: TipRow[]; hint?: string }) {
  const W = 184;
  const left = Math.min(Math.max(x - W / 2, 0), Math.max(width - W, 0));
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 rounded-[10px] border border-[#E4E9F1] bg-white px-3 py-2 text-xs shadow-[0_12px_28px_-12px_rgba(14,26,51,0.35)]"
      style={{ left, top: Math.max(y - 12, 0), width: W, transform: "translateY(-100%)" }}
    >
      <p className="font-mono-num text-[15px] font-bold leading-tight" style={{ color: INK }}>{value}</p>
      <p className="mt-0.5" style={{ color: MUTED }}>{title}</p>
      {rows && rows.length > 0 && (
        <dl className="mt-1.5 space-y-0.5 border-t border-[#EEF2F7] pt-1.5">
          {rows.map((r) => (
            <div key={r.label} className="flex justify-between gap-2">
              <dt style={{ color: MUTED }}>{r.label}</dt>
              <dd className="font-mono-num font-semibold" style={{ color: INK }}>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {hint && <p className="mt-1.5 text-[11px] font-medium" style={{ color: SERIES }}>{hint}</p>}
    </div>
  );
}

const onActivate = (fn?: () => void) => (e: KeyboardEvent) => {
  if (fn && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    fn();
  }
};

export interface SeriesPoint {
  key: string;
  /** Short axis label ("28 Sep", "Wk of 22 Sep", "Sep"). */
  label: string;
  /** Full label for the tooltip ("Mon, 28 Sep 2026"). */
  title: string;
  value: number;
  rows?: TipRow[];
}

/**
 * Columns over time — one measure, one axis. The whole band of a bucket is
 * its hit target (not just the painted column), so a zero-height bucket is
 * still hoverable and clickable.
 */
export function ColumnChart({
  points,
  format = (v) => String(v),
  axisFormat = compactCount,
  onSelect,
  height = 220,
  ariaLabel,
}: {
  points: SeriesPoint[];
  format?: (v: number) => string;
  axisFormat?: (v: number) => string;
  onSelect?: (p: SeriesPoint) => void;
  height?: number;
  ariaLabel: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(0, ...points.map((p) => p.value));
  const ticks = useMemo(() => niceTicks(max), [max]);
  const top = ticks[ticks.length - 1] || 1;
  const PAD_L = 44, PAD_R = 8, PAD_T = 10, PAD_B = 26;
  const plotW = Math.max(width - PAD_L - PAD_R, 10);
  const plotH = height - PAD_T - PAD_B;
  const band = plotW / Math.max(points.length, 1);
  const barW = Math.max(Math.min(24, band * 0.62), 2);
  const y = (v: number) => PAD_T + plotH - (v / top) * plotH;
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(Math.floor(plotW / 64), 1)));

  return (
    <div ref={ref} className="relative w-full select-none" onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block overflow-visible">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD_L} x2={width - PAD_R} y1={y(t)} y2={y(t)} stroke={t === 0 ? BASELINE : GRID} strokeWidth={1} />
              <text x={PAD_L - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill={AXIS} className="font-mono-num">
                {axisFormat(t)}
              </text>
            </g>
          ))}
          {points.map((p, i) => {
            const cx = PAD_L + band * i + band / 2;
            const h = Math.max(y(0) - y(p.value), p.value > 0 ? 2 : 0);
            const x0 = cx - barW / 2;
            const r = Math.min(4, barW / 2, h);
            const yTop = y(0) - h;
            const path = h > 0
              ? `M${x0},${y(0)} V${yTop + r} Q${x0},${yTop} ${x0 + r},${yTop} H${x0 + barW - r} Q${x0 + barW},${yTop} ${x0 + barW},${yTop + r} V${y(0)} Z`
              : "";
            const active = hover === i;
            return (
              <g key={p.key}>
                {path && <path d={path} fill={SERIES} opacity={hover == null || active ? 1 : 0.55} />}
                {i % labelEvery === 0 && (
                  <text x={cx} y={height - 8} textAnchor="middle" fontSize={11} fill={AXIS}>
                    {p.label}
                  </text>
                )}
                <rect
                  x={PAD_L + band * i}
                  y={PAD_T}
                  width={band}
                  height={plotH}
                  fill={active ? "rgba(10,102,240,0.06)" : "transparent"}
                  className={onSelect ? "cursor-pointer outline-none" : "outline-none"}
                  tabIndex={0}
                  role="button"
                  aria-label={`${p.title}: ${format(p.value)}`}
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  onClick={() => onSelect?.(p)}
                  onKeyDown={onActivate(onSelect ? () => onSelect(p) : undefined)}
                />
              </g>
            );
          })}
        </svg>
      )}
      {hover != null && points[hover] && (
        <Tooltip
          x={PAD_L + band * hover + band / 2}
          y={y(points[hover].value)}
          width={width}
          title={points[hover].title}
          value={format(points[hover].value)}
          rows={points[hover].rows}
          hint={onSelect ? "Click to see these bookings" : undefined}
        />
      )}
    </div>
  );
}

/**
 * A line over time with a soft area wash — the crosshair finds the X: the
 * pointer anywhere in a bucket's column snaps to it, no need to hit the 2px
 * line.
 */
export function LineChart({
  points,
  format = (v) => String(v),
  axisFormat = compactCount,
  onSelect,
  height = 220,
  ariaLabel,
  selectHint = "Click to see these bookings",
}: {
  points: SeriesPoint[];
  format?: (v: number) => string;
  axisFormat?: (v: number) => string;
  onSelect?: (p: SeriesPoint) => void;
  height?: number;
  ariaLabel: string;
  selectHint?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(0, ...points.map((p) => p.value));
  const ticks = useMemo(() => niceTicks(max), [max]);
  const top = ticks[ticks.length - 1] || 1;
  const PAD_L = 52, PAD_R = 12, PAD_T = 10, PAD_B = 26;
  const plotW = Math.max(width - PAD_L - PAD_R, 10);
  const plotH = height - PAD_T - PAD_B;
  const band = plotW / Math.max(points.length, 1);
  const x = (i: number) => PAD_L + band * i + band / 2;
  const y = (v: number) => PAD_T + plotH - (v / top) * plotH;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
  const area = points.length ? `${line} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z` : "";
  const labelEvery = Math.max(1, Math.ceil(points.length / Math.max(Math.floor(plotW / 64), 1)));
  const last = points.length - 1;
  const markIndex = hover ?? last;

  return (
    <div ref={ref} className="relative w-full select-none" onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block overflow-visible">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD_L} x2={width - PAD_R} y1={y(t)} y2={y(t)} stroke={t === 0 ? BASELINE : GRID} strokeWidth={1} />
              <text x={PAD_L - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill={AXIS} className="font-mono-num">
                {axisFormat(t)}
              </text>
            </g>
          ))}
          {area && <path d={area} fill={SERIES} opacity={0.1} />}
          {points.length > 1 && <path d={line} fill="none" stroke={SERIES} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
          {hover != null && <line x1={x(hover)} x2={x(hover)} y1={PAD_T} y2={y(0)} stroke={BASELINE} strokeWidth={1} />}
          {points[markIndex] && (
            <circle cx={x(markIndex)} cy={y(points[markIndex].value)} r={4.5} fill={SERIES} stroke="#fff" strokeWidth={2} />
          )}
          {points.map((p, i) => (
            <g key={p.key}>
              {i % labelEvery === 0 && (
                <text x={x(i)} y={height - 8} textAnchor="middle" fontSize={11} fill={AXIS}>
                  {p.label}
                </text>
              )}
              <rect
                x={PAD_L + band * i}
                y={PAD_T}
                width={band}
                height={plotH}
                fill="transparent"
                className={onSelect ? "cursor-pointer outline-none" : "outline-none"}
                tabIndex={0}
                role="button"
                aria-label={`${p.title}: ${format(p.value)}`}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                onClick={() => onSelect?.(p)}
                onKeyDown={onActivate(onSelect ? () => onSelect(p) : undefined)}
              />
            </g>
          ))}
        </svg>
      )}
      {hover != null && points[hover] && (
        <Tooltip
          x={x(hover)}
          y={y(points[hover].value)}
          width={width}
          title={points[hover].title}
          value={format(points[hover].value)}
          rows={points[hover].rows}
          hint={onSelect ? selectHint : undefined}
        />
      )}
    </div>
  );
}

export interface BarRow {
  key: string;
  label: string;
  value: number;
  rows?: TipRow[];
}

/**
 * Ranked horizontal bars for categories (services, car types, centers,
 * channels): label on the left, value at the bar's tip, every bar the same
 * colour (one series). Long tails fold into "Other" past `limit`.
 */
export function BarList({
  rows,
  format = (v) => String(v),
  onSelect,
  limit = 8,
  emptyText = "Nothing in this period.",
}: {
  rows: BarRow[];
  format?: (v: number) => string;
  onSelect?: (r: BarRow) => void;
  limit?: number;
  emptyText?: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const shown = useMemo(() => {
    const sorted = [...rows].sort((a, b) => b.value - a.value);
    if (sorted.length <= limit) return sorted;
    const head = sorted.slice(0, limit - 1);
    const tail = sorted.slice(limit - 1);
    return [...head, { key: "__other", label: `Other (${tail.length})`, value: tail.reduce((s, r) => s + r.value, 0) }];
  }, [rows, limit]);
  const max = Math.max(1, ...shown.map((r) => r.value));
  if (!rows.length || shown.every((r) => r.value === 0)) return <p className="py-8 text-center text-sm text-[#5F6878]">{emptyText}</p>;
  return (
    <ul className="space-y-1">
      {shown.map((r) => {
        const clickable = !!onSelect && r.key !== "__other";
        const active = hover === r.key;
        return (
          <li key={r.key} className="relative">
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onSelect!(r)}
              onMouseEnter={() => setHover(r.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(r.key)}
              onBlur={() => setHover(null)}
              aria-label={`${r.label}: ${format(r.value)}`}
              className={`grid w-full grid-cols-[minmax(0,9.5rem)_1fr] items-center gap-3 rounded-lg px-2 py-1.5 text-left transition-colors disabled:cursor-default sm:grid-cols-[minmax(0,11rem)_1fr] ${
                clickable ? "hover:bg-[#F7F9FC] focus-visible:bg-[#F7F9FC]" : ""
              }`}
            >
              <span className="truncate text-sm text-[#0E1A33]" title={r.label}>
                {r.label}
              </span>
              <span className="flex min-w-0 items-center gap-2">
                <span className="h-2.5 min-w-[3px] rounded-r-[4px]" style={{ width: `${(r.value / max) * 82}%`, background: SERIES, opacity: hover && !active ? 0.55 : 1 }} />
                <span className="shrink-0 font-mono-num text-xs font-semibold text-[#0E1A33]">{format(r.value)}</span>
              </span>
            </button>
            {active && r.rows && r.rows.length > 0 && (
              <div
                role="tooltip"
                className="pointer-events-none absolute right-2 top-0 z-10 w-48 -translate-y-full rounded-[10px] border border-[#E4E9F1] bg-white px-3 py-2 text-xs shadow-[0_12px_28px_-12px_rgba(14,26,51,0.35)]"
              >
                <p className="font-mono-num text-[15px] font-bold leading-tight text-[#0E1A33]">{format(r.value)}</p>
                <p className="mt-0.5 truncate text-[#5F6878]">{r.label}</p>
                <dl className="mt-1.5 space-y-0.5 border-t border-[#EEF2F7] pt-1.5">
                  {r.rows.map((t) => (
                    <div key={t.label} className="flex justify-between gap-2">
                      <dt className="text-[#5F6878]">{t.label}</dt>
                      <dd className="font-mono-num font-semibold text-[#0E1A33]">{t.value}</dd>
                    </div>
                  ))}
                </dl>
                {clickable && <p className="mt-1.5 text-[11px] font-medium text-[#0A66F0]">Click to see these bookings</p>}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Keeps the previous render visible (dimmed) while a refetch runs —
 *  no skeleton flash, no layout jump. */
export function Refetching({ busy, children }: { busy: boolean; children: ReactNode }) {
  const [shown, setShown] = useState(busy);
  useEffect(() => {
    const t = window.setTimeout(() => setShown(busy), busy ? 150 : 0);
    return () => window.clearTimeout(t);
  }, [busy]);
  return <div className={`transition-opacity duration-150 ${shown ? "opacity-50" : "opacity-100"}`}>{children}</div>;
}
