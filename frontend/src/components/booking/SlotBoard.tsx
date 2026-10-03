import { useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight, Timer } from "lucide-react";
import { serviceCenterApi } from "../../api/catalog";
import { formatTime12, todayIST } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { useLiveChannel } from "../../lib/socket";
import { bookingDays, dayParts } from "./bookingTheme";
import type { SlotAvailability } from "../../types";

/**
 * "Available Slots" from the approved booking mockup: a strip of day pills,
 * then the selected day's slots as cards with a radio and an availability
 * chip. The backend never sends raw capacity — "low" (with the real count)
 * only when 2 or fewer are left, so that's the only time a number shows.
 *
 * Every day in the window is fetched up front (same cache key the rest of
 * the app uses) so switching days is instant and fully booked days can say
 * so on the pill. Until the customer picks a day themselves, a day with
 * nothing open moves on to the first day that has something.
 */
export function SlotBoard({
  centerId,
  date,
  onDateChange,
  value,
  onChange,
  maxAdvanceDays,
  heldSeconds,
  notice,
}: {
  centerId: string;
  date: string;
  onDateChange: (date: string) => void;
  value: string;
  onChange: (slotKey: string) => void;
  maxAdvanceDays?: number;
  heldSeconds?: number | null;
  /** A message above the slots (e.g. the slot was lost). */
  notice?: string;
}) {
  const queryClient = useQueryClient();
  const today = todayIST();
  const days = useMemo(() => bookingDays(today, maxAdvanceDays), [today, maxAdvanceDays]);
  const results = useQueries({
    queries: days.map((d) => ({
      queryKey: ["available-slots", centerId, d],
      queryFn: () => serviceCenterApi.availableSlots(centerId, d),
      enabled: !!centerId,
      staleTime: 20_000,
      retry: false,
      refetchInterval: d === date ? 60_000 : false,
    })),
  });
  useLiveChannel(centerId && date ? `slots:${centerId}:${date}` : null, () => {
    queryClient.invalidateQueries({ queryKey: ["available-slots", centerId, date] });
  });

  const openOn = (i: number): boolean | null => {
    const r = results[i];
    if (!r || !r.data) return r?.isError ? false : null;
    return r.data.some((s) => s.status !== "full");
  };

  // The customer's own day pick sticks; until then, skip closed days.
  const touched = useRef(false);
  const dayIndex = days.indexOf(date);
  useEffect(() => {
    if (!centerId) return;
    if (dayIndex < 0) {
      // A stale day (yesterday's restored booking) — start from today.
      onDateChange(days[0]);
      if (value) onChange("");
      return;
    }
    if (touched.current || value) return;
    if (openOn(dayIndex) !== false) return;
    for (let i = 0; i < days.length; i++) {
      const open = openOn(i);
      if (open === null) return; // wait for the earlier days to load
      if (open) {
        if (i !== dayIndex) onDateChange(days[i]);
        return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centerId, dayIndex, value, results.map((r) => r.dataUpdatedAt).join(",")]);

  const strip = useRef<HTMLDivElement>(null);
  const scrollStrip = (dir: 1 | -1) => strip.current?.scrollBy({ left: dir * 240, behavior: "smooth" });
  // Arrows only when the days don't all fit, and only towards hidden ones.
  const [edges, setEdges] = useState({ left: false, right: false });
  useEffect(() => {
    const el = strip.current;
    if (!el) return;
    const measure = () => setEdges({ left: el.scrollLeft > 4, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      ro.disconnect();
    };
  }, [days.length]);
  useEffect(() => {
    // Keep the chosen pill in view (a restored or auto-moved day).
    const el = strip.current?.querySelector<HTMLElement>('[aria-pressed="true"]');
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [date]);

  const current = dayIndex >= 0 ? results[dayIndex] : undefined;
  const slots: SlotAvailability[] = current?.data || [];

  return (
    <div className="@container min-w-0">
      {/* Day pills */}
      <div className="relative">
        <div ref={strip} className="hide-scrollbar -mx-1 flex snap-x gap-2 overflow-x-auto px-1 pb-1" role="group" aria-label="Choose a day">
          {days.map((d, i) => {
            const { top, bottom } = dayParts(d, today);
            const selected = d === date;
            const closed = openOn(i) === false;
            return (
              <button
                key={d}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  touched.current = true;
                  if (d !== date) {
                    onDateChange(d);
                    onChange("");
                  }
                }}
                className={`flex h-[58px] w-[76px] shrink-0 snap-start flex-col items-center justify-center rounded-[12px] border text-center transition ${
                  selected
                    ? "border-[#0A66F0] bg-[#0A66F0] text-white shadow-[0_8px_18px_-10px_rgba(10,102,240,0.8)]"
                    : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:border-[#0A66F0]/50"
                }`}
              >
                <span className={`text-[12px] font-semibold ${selected ? "text-white" : "text-[#0E1A33]"}`}>{top}</span>
                <span className={`text-[12px] ${selected ? "text-white/85" : closed ? "text-[#9AA3B2]" : "text-[#5F6878]"}`}>{closed && !selected ? "Full" : bottom}</span>
              </button>
            );
          })}
        </div>
        {edges.left && (
          <button type="button" aria-label="Earlier days" onClick={() => scrollStrip(-1)} className="absolute left-0 top-[13px] flex h-8 w-8 items-center justify-center rounded-full border border-[#E4E9F1] bg-white text-[#0E1A33] shadow-md hover:border-[#0A66F0]">
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
        {edges.right && (
          <button type="button" aria-label="Later days" onClick={() => scrollStrip(1)} className="absolute right-0 top-[13px] flex h-8 w-8 items-center justify-center rounded-full border border-[#E4E9F1] bg-white text-[#0E1A33] shadow-md hover:border-[#0A66F0]">
            <ChevronRight className="h-4 w-4" />
          </button>
        )}
      </div>

      {notice && <p className="mt-3 rounded-[12px] bg-[#FFF6D6] px-3 py-2 text-[13px] font-medium text-[#7A5B00]">{notice}</p>}

      {/* Slots */}
      <div className="mt-3">
        {!current || current.isLoading ? (
          <div className="grid grid-cols-1 gap-2.5 @md:grid-cols-2 @2xl:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[54px] animate-pulse rounded-[14px] bg-[#F1F4F9] @2xl:h-[96px]" />
            ))}
          </div>
        ) : current.isError ? (
          <p className="text-[13px] text-[var(--color-error)]">{getErrorMessage(current.error)}</p>
        ) : !slots.length ? (
          <p className="flex items-center gap-2 text-[13px] text-[#5F6878]">
            <CalendarDays className="h-4 w-4" /> No slots on this day — try another.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-2.5 @md:grid-cols-2 @2xl:grid-cols-4" role="radiogroup" aria-label="Time slot">
            {slots.map((s) => {
              const full = s.status === "full";
              const selected = value === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={full}
                  onClick={() => onChange(s.key)}
                  className={`group flex items-center gap-3 rounded-[14px] border px-3.5 py-3 text-left transition @2xl:flex-col @2xl:items-start @2xl:gap-2 @2xl:px-4 @2xl:py-3.5 ${
                    full
                      ? "cursor-not-allowed border-[#EEF1F5] bg-[#F8FAFC] text-[#9AA3B2]"
                      : selected
                        ? "border-[#0A66F0] bg-[#F3F7FF] ring-1 ring-[#0A66F0]"
                        : "border-[#E4E9F1] bg-white hover:border-[#0A66F0]/60"
                  }`}
                >
                  <span className="flex min-w-0 flex-1 items-center gap-3 @2xl:w-full">
                    <span
                      aria-hidden
                      className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full border-2 ${
                        selected ? "border-[#0A66F0]" : full ? "border-[#D5DBE5]" : "border-[#C3CBD8]"
                      }`}
                    >
                      {selected && <span className="h-2 w-2 rounded-full bg-[#0A66F0]" />}
                    </span>
                    <span className="min-w-0">
                      <span className={`block whitespace-nowrap text-[14px] font-semibold @2xl:text-[16px] ${full ? "" : "text-[#0E1A33]"}`}>
                        {formatTime12(s.start)}
                        <span className="font-medium @2xl:hidden"> – {formatTime12(s.end)}</span>
                      </span>
                      <span className={`hidden text-[13px] @2xl:block ${full ? "" : "text-[#5F6878]"}`}>– {formatTime12(s.end)}</span>
                    </span>
                  </span>
                  <SlotChip slot={s} />
                </button>
              );
            })}
          </div>
        )}
        {!!value && heldSeconds != null && heldSeconds > 0 && (
          <p className="mt-2.5 inline-flex items-center gap-1.5 text-[12px] text-[#5F6878]">
            <Timer className="h-3.5 w-3.5 text-[#0A66F0]" />
            Held for you · {Math.floor(heldSeconds / 60)}:{String(heldSeconds % 60).padStart(2, "0")}
          </p>
        )}
      </div>
    </div>
  );
}

function SlotChip({ slot }: { slot: SlotAvailability }) {
  if (slot.status === "full") return <span className="shrink-0 rounded-full bg-[#EEF1F5] px-2.5 py-1 text-[11px] font-semibold text-[#8A93A3] @2xl:ml-[30px]">Full</span>;
  if (slot.status === "low" && slot.remaining != null)
    return (
      <span className="shrink-0 rounded-full bg-[#FFF1D6] px-2.5 py-1 text-[11px] font-semibold text-[#A15C00] @2xl:ml-[30px]">
        {slot.remaining} Left
      </span>
    );
  return <span className="shrink-0 rounded-full bg-[#E5F6EC] px-2.5 py-1 text-[11px] font-semibold text-[#12804A] @2xl:ml-[30px]">Available</span>;
}
