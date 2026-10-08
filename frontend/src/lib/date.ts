const IST_TZ = "Asia/Kolkata";

export function format(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: IST_TZ });
  } catch {
    return dateStr;
  }
}

/** A server instant sent without a zone ("2026-10-07T10:56:33") is UTC
 *  (that's how Mongo stores it) — never the browser's local time. Strings
 *  that already carry Z / an offset, and plain dates, pass through. */
export function asUtcInstant(iso: string): string {
  return /T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(iso) ? `${iso}Z` : iso;
}

/** "7 Oct 2026, 2:35 PM" (IST) — the same 12-hour clock as every other
 *  person-facing time (en-IN alone gives "02:35 pm"). */
export function formatDateTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) return dateStr;
    const day = d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: IST_TZ });
    const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: IST_TZ });
    return `${day}, ${time}`;
  } catch {
    return dateStr;
  }
}

/**
 * A booking's day as a person says it: "Today", "Tomorrow", else
 * "Tue, 30 Sep". Reads the first 10 characters (scheduled_date is an
 * IST-digit string), anchored at IST noon so no browser timezone can shift it.
 */
export function formatDay(dateStr?: string | null): string {
  const key = String(dateStr ?? "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return String(dateStr ?? "");
  const today = todayIST();
  if (key === today) return "Today";
  const tomorrow = new Intl.DateTimeFormat("en-CA", { timeZone: IST_TZ }).format(new Date(Date.now() + 24 * 60 * 60 * 1000));
  if (key === tomorrow) return "Tomorrow";
  const d = new Date(`${key}T12:00:00+05:30`);
  const sameYear = key.slice(0, 4) === today.slice(0, 4);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }), timeZone: IST_TZ });
}

/** "26 Sept" (the year only when it isn't this one) — for compact rows. */
export function formatShortDate(dateStr?: string | null): string {
  const key = String(dateStr ?? "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return String(dateStr ?? "");
  const sameYear = key.slice(0, 4) === todayIST().slice(0, 4);
  return new Date(`${key}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }), timeZone: IST_TZ });
}

/** "14:30" -> "2:30 PM" — the 12-hour clock every person-facing time uses. */
export function formatTime12(hhmm?: string | null, compact = false): string {
  const [h, m] = String(hhmm ?? "").split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return String(hhmm ?? "");
  const period = h >= 12 ? "PM" : "AM";
  const hour12 = h % 12 || 12;
  return compact && m === 0 ? `${hour12} ${period}` : `${hour12}:${String(m).padStart(2, "0")} ${period}`;
}

/** A slot key "09:00-12:00" as "9:00 AM – 12:00 PM". Display only — the key stays 24-hour. */
export function formatSlot(slot?: string | null): string {
  const text = String(slot ?? "").trim();
  if (!text.includes("-")) return text;
  const [start, end] = text.split("-", 2);
  return `${formatTime12(start.trim())} – ${formatTime12(end.trim())}`;
}

/** A clock reading (ISO instant) as IST 12-hour time, e.g. "2:30 PM". */
export function formatClockIST(iso?: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: IST_TZ });
  } catch {
    return "";
  }
}

/**
 * "Today" as YYYY-MM-DD in IST, regardless of the viewer's browser timezone
 * — use this anywhere a date-input's `min`/default needs to reflect the IST
 * business day (matches the backend's now_ist()). Do NOT use
 * `new Date().toISOString().split("T")[0]` for this — that's UTC calendar
 * date, which is wrong for the first ~5.5 hours of every IST day.
 */
export function todayIST(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: IST_TZ }).format(new Date());
}

/** The IST calendar date `days` days before today (YYYY-MM-DD). */
export function daysAgoIST(days: number): string {
  const d = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: IST_TZ }).format(d);
}

/** The current IST wall-clock time as "HH:MM" (24h). */
export function nowTimeIST(): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: IST_TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date());
}

/**
 * Last bookable date (inclusive), mirroring the server's `max_advance_days`
 * booking-policy default of 7 — today + the next 6 days. The backend is the
 * authority (it rejects anything beyond the window at every entry point);
 * this only keeps the date input's range honest so customers/managers never
 * pick a date the submit would bounce.
 */
export function maxBookingDateIST(days = 7): string {
  const d = new Date(Date.now() + (days - 1) * 24 * 60 * 60 * 1000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: IST_TZ }).format(d);
}

/**
 * Minutes from now until a booking's slot actually starts (IST-anchored,
 * same "+05:30 offset string" approach as the booking-time validation in
 * NewBookingPage — Date.now() is always a true UTC epoch, no conversion
 * needed on that side). Negative means the slot has already started.
 */
export function minutesUntilSlotStart(scheduledDate: string, scheduledSlot: string): number {
  const datePart = scheduledDate.slice(0, 10);
  const startTime = scheduledSlot.split("-")[0]?.trim();
  if (!datePart || !startTime) return Infinity;
  const startMs = new Date(`${datePart}T${startTime}:00+05:30`).getTime();
  if (Number.isNaN(startMs)) return Infinity;
  return (startMs - Date.now()) / 60000;
}

// A booking that still needs a captain and starts within this many minutes
// is genuinely urgent — a captain can't even start heading out more than 30
// minutes before the slot anyway, so anything inside that window has
// essentially no lead time left.
export const URGENT_ASSIGNMENT_MINUTES = 30;
