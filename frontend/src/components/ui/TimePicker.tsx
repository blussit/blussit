import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Clock, X } from "lucide-react";
import { cn } from "../../lib/cn";

const ALL_HOURS_12 = Array.from({ length: 12 }, (_, i) => i + 1); // 1-12
const ALL_MINUTES = Array.from({ length: 12 }, (_, i) => i * 5); // 0,5,...,55
const PERIODS: ("AM" | "PM")[] = ["AM", "PM"];

interface Parsed {
  hour12: number;
  minute: number;
  period: "AM" | "PM";
}

function to24(hour12: number, period: "AM" | "PM"): number {
  const h = hour12 % 12;
  return period === "PM" ? h + 12 : h;
}

function to12(hour24: number): { hour12: number; period: "AM" | "PM" } {
  const period: "AM" | "PM" = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return { hour12, period };
}

function parseValue(value: string | undefined): Parsed | null {
  if (!value) return null;
  const [hStr, mStr] = value.split(":");
  const h24 = Number(hStr);
  const m = Number(mStr);
  if (Number.isNaN(h24) || Number.isNaN(m)) return null;
  // Snap to the nearest 5-minute stop shown in the picker's minute column.
  const minute = Math.round(m / 5) * 5;
  return { ...to12(h24), minute: minute === 60 ? 0 : minute };
}

function toValue({ hour12, minute, period }: Parsed): string {
  return `${String(to24(hour12, period)).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function displayLabel(value: string | undefined): string {
  const parsed = parseValue(value);
  if (!parsed) return "";
  return `${String(parsed.hour12).padStart(2, "0")}:${String(parsed.minute).padStart(2, "0")} ${parsed.period}`;
}

function parseHM(hm: string): { h: number; m: number } {
  const [h, m] = hm.split(":").map(Number);
  return { h: h || 0, m: m || 0 };
}

export interface TimePickerProps {
  label?: string;
  value?: string; // "HH:MM", 24-hour — same contract as native <input type="time">
  onChange: (value: string) => void;
  error?: string;
  required?: boolean;
  disabled?: boolean;
  placeholder?: string;
  id?: string;
  className?: string;
  // Bounds the pickable range to a store's working hours ("HH:MM", 24h) —
  // when set, the Hour column lists every hour in that range in one
  // continuous chronological sequence (e.g. 7,8,9,10,11,12,1,...,8 for a
  // 7am-8pm store) and AM/PM is inferred from each hour's actual position
  // instead of asking the user to pick a period first. The AM/PM column is
  // still there for an explicit override/jump.
  minTime?: string;
  maxTime?: string;
}

interface PanelPosition {
  top: number;
  left: number;
}

// Same in-DOM, fixed-position, theme-matched approach as DatePicker/Select —
// native <input type="time"> hands its picker to the OS/webview shell too,
// which is what let it render detached and cut off the visible panel.
export function TimePicker({
  label,
  value,
  onChange,
  error,
  required,
  disabled,
  placeholder = "Select time",
  id,
  className,
  minTime,
  maxTime,
}: TimePickerProps) {
  const fieldId = id || label?.toLowerCase().replace(/\s+/g, "-");
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PanelPosition | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const selected = parseValue(value);
  const ranged = Boolean(minTime || maxTime);
  const minHM = minTime ? parseHM(minTime) : { h: 0, m: 0 };
  const maxHM = maxTime ? parseHM(maxTime) : { h: 23, m: 59 };

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

  // Positioned in real viewport pixels, clamped inside the visible viewport
  // on every edge — see DatePicker.tsx for why this replaced CSS anchoring.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const margin = 8;
    const panelWidth = 232;
    const panelHeight = 300;
    const compute = () => {
      if (!wrapperRef.current) return;
      const rect = wrapperRef.current.getBoundingClientRect();
      const viewportW = window.innerWidth;
      const viewportH = window.innerHeight;
      const openUpward = viewportH - rect.bottom < panelHeight && rect.top > panelHeight;
      const top = openUpward ? Math.max(margin, rect.top - panelHeight - 6) : Math.min(rect.bottom + 6, viewportH - panelHeight - margin);
      const left = Math.min(Math.max(rect.left, margin), Math.max(margin, viewportW - panelWidth - margin));
      setPos({ top, left });
    };
    compute();
    const onResize = () => compute();
    // Close only when the PAGE scrolls out from under the trigger — a scroll
    // event whose target is inside our own panel is the user scrolling one
    // of the hour/minute columns, which must NOT close the picker.
    const onScroll = (e: Event) => {
      if (panelRef.current && e.target instanceof Node && panelRef.current.contains(e.target)) return;
      setOpen(false);
    };
    window.addEventListener("resize", onResize);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  // Every hour in the working-hours range, in one chronological sequence —
  // each entry already knows its own AM/PM, so e.g. "7" can legitimately
  // appear twice (7 AM near the top, 7 PM further down) for a 7am-8pm store,
  // each a distinct, unambiguous row.
  const rangedEntries = ranged
    ? Array.from({ length: Math.max(0, maxHM.h - minHM.h) + 1 }, (_, i) => {
        const hour24 = minHM.h + i;
        return { hour24, ...to12(hour24) };
      })
    : [];

  const currentHour24 = selected ? to24(selected.hour12, selected.period) : null;

  const minutesFor24 = (hour24: number) => {
    let list = ALL_MINUTES;
    if (hour24 === minHM.h) list = list.filter((m) => m >= Math.ceil(minHM.m / 5) * 5);
    if (hour24 === maxHM.h) list = list.filter((m) => m <= Math.floor(maxHM.m / 5) * 5);
    return list.length ? list : [hour24 === minHM.h ? Math.ceil(minHM.m / 5) * 5 : 0];
  };

  const pickRangedHour = (entry: { hour24: number; hour12: number; period: "AM" | "PM" }) => {
    const validMinutes = minutesFor24(entry.hour24);
    const minute = selected && validMinutes.includes(selected.minute) ? selected.minute : validMinutes[0];
    onChange(toValue({ hour12: entry.hour12, period: entry.period, minute }));
  };

  const pickRangedMinute = (minute: number) => {
    const entry = rangedEntries.find((e) => e.hour24 === currentHour24) ?? rangedEntries[0];
    if (!entry) return;
    onChange(toValue({ hour12: entry.hour12, period: entry.period, minute }));
  };

  const pickRangedPeriod = (period: "AM" | "PM") => {
    // Prefer keeping the same hour number if that exact hour also falls in
    // the target period within range; otherwise land on the first hour
    // available in that period.
    const sameHour = selected ? rangedEntries.find((e) => e.period === period && e.hour12 === selected.hour12) : undefined;
    const entry = sameHour ?? rangedEntries.find((e) => e.period === period);
    if (entry) pickRangedHour(entry);
  };

  // Unranged mode — plain independent Hour(1-12)/Minute/Period columns, no
  // range to infer anything from.
  const set = (patch: Partial<Parsed>) => {
    const base = selected ?? { hour12: 12, minute: 0, period: "AM" as const };
    onChange(toValue({ ...base, ...patch }));
  };

  const columnBtn = (active: boolean) =>
    cn(
      "w-full rounded-lg px-3 py-1.5 text-center text-sm transition-colors",
      active ? "bg-[var(--color-primary)] font-medium text-white" : "text-[var(--color-text-primary)] hover:bg-gray-100"
    );

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
            error ? "border-[var(--color-error)]" : "border-gray-300",
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
            <Clock className="h-4 w-4 text-gray-400" />
          </span>
        </button>

        {open && pos && (
          <div
            ref={panelRef}
            role="dialog"
            style={{ position: "fixed", top: pos.top, left: pos.left, width: 232 }}
            className="z-50 rounded-xl border border-gray-200 bg-[var(--color-surface,#fff)] p-2 shadow-lg"
          >
            {ranged && minTime && maxTime && (
              <p className="mb-1.5 px-1 text-[11px] text-[var(--color-text-secondary)]">
                Store hours: {displayLabel(minTime)} – {displayLabel(maxTime)}
              </p>
            )}
            <div className="grid grid-cols-3 gap-1.5 text-center">
              <span className="text-xs font-medium text-gray-400">Hour</span>
              <span className="text-xs font-medium text-gray-400">Min</span>
              <span className="text-xs font-medium text-gray-400">&nbsp;</span>

              {/* min-h-0 is required here — inside a grid row, an overflow-y-auto
                  item otherwise sizes to its full content height instead of
                  respecting max-h, and never actually scrolls. */}
              <div className="col-span-1 min-h-0 max-h-52 space-y-0.5 overflow-y-auto overscroll-contain touch-pan-y">
                {ranged
                  ? rangedEntries.map((entry) => (
                      <button
                        key={entry.hour24}
                        type="button"
                        onClick={() => pickRangedHour(entry)}
                        className={columnBtn(currentHour24 === entry.hour24)}
                      >
                        {String(entry.hour12).padStart(2, "0")}
                      </button>
                    ))
                  : ALL_HOURS_12.map((h) => (
                      <button key={h} type="button" onClick={() => set({ hour12: h })} className={columnBtn(selected?.hour12 === h)}>
                        {String(h).padStart(2, "0")}
                      </button>
                    ))}
              </div>
              <div className="col-span-1 min-h-0 max-h-52 space-y-0.5 overflow-y-auto overscroll-contain touch-pan-y">
                {(ranged ? (currentHour24 !== null ? minutesFor24(currentHour24) : ALL_MINUTES) : ALL_MINUTES).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => (ranged ? pickRangedMinute(m) : set({ minute: m }))}
                    className={columnBtn(selected?.minute === m)}
                  >
                    {String(m).padStart(2, "0")}
                  </button>
                ))}
              </div>
              <div className="col-span-1 min-h-0 space-y-0.5">
                {PERIODS.map((p) => {
                  const availableInRange = !ranged || rangedEntries.some((e) => e.period === p);
                  return (
                    <button
                      key={p}
                      type="button"
                      disabled={!availableInRange}
                      onClick={() => (ranged ? pickRangedPeriod(p) : set({ period: p }))}
                      className={cn(columnBtn(selected?.period === p), !availableInRange && "cursor-not-allowed text-gray-300 hover:bg-transparent")}
                    >
                      {p}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="mt-2 flex items-center justify-between border-t border-gray-100 pt-2">
              <button
                type="button"
                onClick={() => {
                  const now = new Date();
                  let h24 = now.getHours();
                  let minute = Math.round(now.getMinutes() / 5) * 5;
                  if (minute === 60) {
                    minute = 0;
                    h24 = (h24 + 1) % 24;
                  }
                  if (ranged) {
                    h24 = Math.min(Math.max(h24, minHM.h), maxHM.h);
                    const entry = rangedEntries.find((e) => e.hour24 === h24);
                    if (entry) onChange(toValue({ hour12: entry.hour12, period: entry.period, minute: minutesFor24(h24).includes(minute) ? minute : minutesFor24(h24)[0] }));
                  } else {
                    const { hour12, period } = to12(h24);
                    onChange(toValue({ hour12, minute, period }));
                  }
                }}
                className="text-xs font-medium text-[var(--color-primary)] hover:underline"
              >
                Now
              </button>
              <button type="button" onClick={() => setOpen(false)} className="text-xs font-medium text-gray-400 hover:text-[var(--color-text-primary)]">
                Done
              </button>
            </div>
          </div>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-[var(--color-error)]">{error}</p>}
    </div>
  );
}
