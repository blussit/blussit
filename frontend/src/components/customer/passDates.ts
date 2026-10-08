/**
 * A pass's dates in the customer's words (founder 2026-10-07): every pass
 * screen shows its end as "Last Booking Day: 18 Oct 2026" — the server's
 * `last_booking_day_label` (extension included), falling back to
 * `last_bookable_day`, then the period's `end_date` (older responses).
 */
import { format, todayIST } from "../../lib/date";

export interface PassDateFields {
  last_booking_day_label?: string | null;
  last_bookable_day?: string | null;
  end_date?: string | null;
}

/** "07 Oct 2026" → "7 Oct 2026". The server sends "7 Oct 2026" since
 *  2026-10-08; kept as a harmless guard for older responses. */
const unpad = (label: string) => label.trim().replace(/^0(\d)\b/, "$1");

/** A day as "18 Oct 2026": the server's label, else a YYYY-MM-DD / ISO value. */
export function dayLabel(label?: string | null, iso?: string | null): string {
  if (label && label.trim()) return unpad(label);
  if (!iso) return "";
  const key = iso.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(key) && iso.length === 10 ? format(`${key}T12:00:00+05:30`) : format(iso);
}

/** "18 Oct 2026" — the last day a wash can be booked on this pass. */
export function lastBookingDay(p: PassDateFields): string {
  return dayLabel(p.last_booking_day_label, p.last_bookable_day) || (p.end_date ? format(p.end_date) : "");
}

/** "Last Booking Day: 18 Oct 2026" (empty when the pass has no end). */
export function lastBookingDayText(p: PassDateFields): string {
  const day = lastBookingDay(p);
  return day ? `Last Booking Day: ${day}` : "";
}

/** Whole days from today (IST) to a YYYY-MM-DD day: 0 = today, negative = past. */
export function daysUntil(day?: string | null): number | null {
  const key = String(day || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return null;
  const ms = Date.parse(`${key}T00:00:00Z`) - Date.parse(`${todayIST()}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}
