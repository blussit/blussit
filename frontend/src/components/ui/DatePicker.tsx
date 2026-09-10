import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-react";
import { cn } from "../../lib/cn";
import { todayIST } from "../../lib/date";

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const WEEKDAY_LABELS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

interface DateKey {
  y: number;
  m: number; // 1-12
  d: number;
}

function parseKey(key: string | undefined): DateKey | null {
  if (!key) return null;
  const [y, m, d] = key.split("-").map(Number);
  if (!y || !m || !d) return null;
  return { y, m, d };
}

function toKey({ y, m, d }: DateKey): string {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function displayLabel(key: string | undefined): string {
  const parsed = parseKey(key);
  if (!parsed) return "";
  return `${parsed.d} ${MONTH_NAMES[parsed.m - 1].slice(0, 3)} ${parsed.y}`;
}

// Pure calendar-grid math on plain y/m/d integers — never routed through a
// timezone-aware Date parse, so it can't fall into the same "digit string
// silently reinterpreted as UTC/local" trap the rest of the app's IST dates
// had to be fixed for. new Date(y, m-1, 1) here is only ever used for its
// day-of-week, which is timezone-independent local calendar math.
function daysInMonth(y: number, m: number): number {
  return new Date(y, m, 0).getDate();
}
function firstWeekday(y: number, m: number): number {
  return new Date(y, m - 1, 1).getDay();
}

export interface DatePickerProps {
  label?: string;
  value?: string;
  onChange: (value: string) => void;
  min?: string;
  max?: string;
  error?: string;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  id?: string;
  className?: string;
}

// A calendar built entirely in-DOM, styled to match Select's trigger/panel
// exactly (rounded-xl border, primary focus ring, rounded-lg items) — native
// <input type="date"> hands its calendar popup to the OS/webview shell the
// same way native <select> did, which is what made it render detached and
// off-panel. This keeps everything anchored, themed, and on-screen.
interface PanelPosition {
  top: number;
  left: number;
  width: number;
  openUpward: boolean;
}

export function DatePicker({ label, value, onChange, min, max, error, required, disabled, placeholder = "Select date", id, className }: DatePickerProps) {
  const fieldId = id || label?.toLowerCase().replace(/\s+/g, "-");
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PanelPosition | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const selected = parseKey(value);
  const todayKey = todayIST();
  const initialView = selected ?? parseKey(todayKey)!;
  const [viewY, setViewY] = useState(initialView.y);
  const [viewM, setViewM] = useState(initialView.m);

  useEffect(() => {
    if (!open) return;
    const base = selected ?? parseKey(todayKey)!;
    setViewY(base.y);
    setViewM(base.m);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Positioned with `position: fixed` in real viewport pixels — computed
  // fresh each time, not CSS-anchored to the trigger's own stacking context
  // — so it always lands fully inside the visible viewport (never clipped
  // by an ancestor's width, never bleeding past the right/bottom edge) and
  // shrinks to fit narrow/mobile viewports instead of overflowing them.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const margin = 8;
    const estimatedHeight = 330;
    const compute = () => {
      if (!wrapperRef.current) return;
      const rect = wrapperRef.current.getBoundingClientRect();
      const viewportW = window.innerWidth;
      const viewportH = window.innerHeight;
      const width = Math.max(272, Math.min(288, viewportW - margin * 2));
      const openUpward = viewportH - rect.bottom < estimatedHeight && rect.top > estimatedHeight;
      const top = openUpward ? Math.max(margin, rect.top - estimatedHeight - 6) : rect.bottom + 6;
      const left = Math.min(Math.max(rect.left, margin), Math.max(margin, viewportW - width - margin));
      setPos({ top, left, width, openUpward });
    };
    compute();
    // Recompute on resize (orientation change, keyboard opening on mobile);
    // close on scroll rather than tracking it — the trigger itself scrolled
    // out from under the calendar, so re-anchoring would feel like it jumped.
    const onResize = () => compute();
    const onScroll = () => setOpen(false);
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  const goMonth = (delta: number) => {
    let y = viewY;
    let m = viewM + delta;
    if (m < 1) {
      m = 12;
      y -= 1;
    } else if (m > 12) {
      m = 1;
      y += 1;
    }
    setViewY(y);
    setViewM(m);
  };

  const pick = (d: number) => {
    onChange(toKey({ y: viewY, m: viewM, d }));
    setOpen(false);
  };

  const leadBlanks = firstWeekday(viewY, viewM);
  const totalDays = daysInMonth(viewY, viewM);
  const cells: (number | null)[] = [...Array(leadBlanks).fill(null), ...Array.from({ length: totalDays }, (_, i) => i + 1)];

  return (
    <div className="w-full">
      {label && (
        <label htmlFor={fieldId} className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
          {label}
        </label>
      )}
      <div className="relative" ref={wrapperRef}>
        <button
          type="button"
          id={fieldId}
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="dialog"
          aria-expanded={open}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-xl border bg-[var(--color-surface,#fff)] px-3.5 py-2.5 text-left text-sm transition-colors",
            "focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]",
            disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:border-[var(--color-primary)]",
            error ? "border-[var(--color-error)]" : "border-[#F3E5B5]",
            className
          )}
        >
          <span className={cn("truncate", value ? "text-[var(--color-text-primary)]" : "text-gray-400")}>
            {value ? displayLabel(value) : placeholder}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            {value && !required && (
              <X
                className="h-3.5 w-3.5 text-gray-400 hover:text-[var(--color-text-primary)]"
                onClick={(e) => {
                  e.stopPropagation();
                  onChange("");
                }}
              />
            )}
            <Calendar className="h-4 w-4 text-gray-400" />
          </span>
        </button>

        {open && pos && (
          <div
            role="dialog"
            style={{ position: "fixed", top: pos.top, left: pos.left, width: pos.width }}
            className="z-50 rounded-xl border border-[#F3E5B5] bg-white p-3 shadow-[0_12px_32px_-8px_rgba(0,0,0,0.18)]"
          >
            <div className="mb-2 flex items-center justify-between">
              <button
                type="button"
                onClick={() => goMonth(-1)}
                className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-[var(--color-text-primary)]"
                aria-label="Previous month"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {MONTH_NAMES[viewM - 1]} {viewY}
              </span>
              <button
                type="button"
                onClick={() => goMonth(1)}
                className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-[var(--color-text-primary)]"
                aria-label="Next month"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-7 gap-y-1 text-center">
              {WEEKDAY_LABELS.map((wd) => (
                <span key={wd} className="text-xs font-medium text-gray-400">
                  {wd}
                </span>
              ))}
              {cells.map((d, i) => {
                if (d === null) return <span key={`blank-${i}`} />;
                const key = toKey({ y: viewY, m: viewM, d });
                const isSelected = key === value;
                const isToday = key === todayKey;
                const isDisabled = (min && key < min) || (max && key > max);
                return (
                  <button
                    key={key}
                    type="button"
                    disabled={Boolean(isDisabled)}
                    onClick={() => pick(d)}
                    className={cn(
                      "mx-auto flex h-8 w-8 items-center justify-center rounded-lg text-sm transition-colors",
                      isDisabled
                        ? "cursor-not-allowed text-gray-300"
                        : isSelected
                          ? "bg-[var(--color-primary)] font-medium text-white"
                          : isToday
                            ? "border border-[var(--color-primary)] text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10"
                            : "text-[var(--color-text-primary)] hover:bg-gray-100"
                    )}
                  >
                    {d}
                  </button>
                );
              })}
            </div>

            <div className="mt-2 flex items-center justify-between border-t border-[#F3E5B5] pt-2">
              <button
                type="button"
                onClick={() => {
                  const t = parseKey(todayKey)!;
                  if ((min && todayKey < min) || (max && todayKey > max)) return;
                  setViewY(t.y);
                  setViewM(t.m);
                  onChange(todayKey);
                  setOpen(false);
                }}
                className="text-xs font-medium text-[var(--color-primary)] hover:underline"
              >
                Today
              </button>
              {value && !required && (
                <button
                  type="button"
                  onClick={() => {
                    onChange("");
                    setOpen(false);
                  }}
                  className="text-xs font-medium text-gray-400 hover:text-[var(--color-text-primary)]"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-[var(--color-error)]">{error}</p>}
    </div>
  );
}
