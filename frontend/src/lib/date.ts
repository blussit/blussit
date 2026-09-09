const IST_TZ = "Asia/Kolkata";

export function format(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: IST_TZ });
  } catch {
    return dateStr;
  }
}

export function formatDateTime(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: IST_TZ });
  } catch {
    return dateStr;
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
