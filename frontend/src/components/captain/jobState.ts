/**
 * The captain job state machine, read straight off the booking — mirrors
 * the backend gates in BookingService (start_heading / verify_vehicle /
 * capture_before_photo / capture_after_photo_and_complete) so the app only
 * ever offers the step the server will actually accept.
 *
 *   assigned                         → "start"  (Start trip = heading, GPS)
 *   captain_on_the_way, not verified → "arrive" (4-digit code + GPS geofence)
 *   captain_on_the_way, verified     → "before" (1 before photo = wash starts)
 *   service_started                  → "after"  (1 after photo = completes)
 *   completed                        → "done"   (collect if still unpaid)
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { vehicleTypeApi } from "../../api/catalog";
import { formatClockIST, formatDay, formatSlot } from "../../lib/date";
import { vehicleLabel } from "../../lib/constants";
import { bookingServiceLabel, societyPlanSuffix, toSlabs, type BookingSlab } from "../../lib/bookingGroups";
import { titleCase } from "../public/landing/shared";
import type { Booking } from "../../types";

export type Step = "start" | "arrive" | "before" | "after" | "done" | "closed";

export function stepOf(b: Booking): Step {
  switch (b.status) {
    case "assigned":
      return "start";
    case "captain_on_the_way":
      return b.vehicle_verified ? "before" : "arrive";
    case "service_started":
      return "after";
    case "completed":
      return "done";
    default:
      return "closed";
  }
}

export const isFinished = (b: Booking) => b.status === "completed" || b.status === "cancelled";

/** Window long gone — the backend refuses every captain action; only a
 * manager can reschedule/reassign it. */
export const needsManager = (b: Booking) => b.issue_flag === "captain_missed_window" && !b.issue_resolved;

// Flags each step lets through (backend: start_heading exempts
// captain_not_started; ARRIVAL_STAGE_EXEMPT_FLAGS; COMPLETION_EXEMPT_FLAGS).
const EXEMPT: Partial<Record<Step, string[]>> = {
  start: ["captain_not_started"],
  arrive: ["captain_delay", "captain_late_start"],
  before: ["captain_delay", "captain_late_start"],
  after: ["service_overrun", "captain_late_start"],
};

/** An open issue flag the server would refuse this step over. */
export function blockedByFlag(b: Booking): boolean {
  const flag = b.issue_flag;
  if (!flag || b.issue_resolved) return false;
  const allowed = EXEMPT[stepOf(b)];
  return !!allowed && !allowed.includes(flag);
}

/** "Star Wash" as a title (+ " · Society plan (…)" on a society pass). */
export const serviceName = (b: Booking) => {
  const name = b.combo_name || b.service_names?.join(", ");
  return name ? `${titleCase(name)}${societyPlanSuffix(b)}` : bookingServiceLabel(b);
};
export const carName = (b: Booking) => vehicleLabel(b);

/** "Pending" / "Half Day" — a raw API status shown as a chip. */
export const statusWord = (s?: string | null) => titleCase((s ?? "").replace(/_/g, " "));

/* ---------------------------------------------- car type + service */

/** Vehicle type names by id (and slug). */
export type CarTypes = ReadonlyMap<string, string>;

// The enriched booking carries the type name (vehicle_type_name); new
// bookings also denormalize it into vehicle_label. "Vehicle" is the
// backend's own fallback, not a type.
const namedType = (b: Booking) =>
  b.vehicle_type_name || (b.vehicle_label && b.vehicle_label !== "Vehicle" ? b.vehicle_label : "");

/** The type list — fetched only when a booking arrives without a type name
 * (older saved-vehicle booking); shared with the rest of the app. */
export function useCarTypes(cars: Booking[]): CarTypes {
  const needed = cars.some((b) => !namedType(b) && !!(b.vehicle_type || b.vehicle_snapshot?.vehicle_type));
  const { data } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), enabled: needed, staleTime: 30 * 60_000 });
  return useMemo(() => {
    const names = new Map<string, string>();
    for (const v of data ?? []) {
      names.set(v.id, v.name);
      names.set(v.slug, v.name);
    }
    return names;
  }, [data]);
}

/** "Sedan" — the car's TYPE; "" when it can't be told. */
export function carType(b: Booking, types?: CarTypes): string {
  const named = namedType(b);
  if (named) return titleCase(named);
  const id = b.vehicle_type || b.vehicle_snapshot?.vehicle_type || "";
  return titleCase(types?.get(id));
}

const makeModel = (b: Booking) => [b.vehicle_snapshot?.brand, b.vehicle_snapshot?.model].filter(Boolean).join(" ");
const plateOf = (b: Booking) => b.vehicle_snapshot?.registration_number || b.vehicle_registration_number || "";

/** The car's headline: its type, else its make ("Honda City"), else "Vehicle". */
export const carTitle = (b: Booking, types?: CarTypes) => carType(b, types) || makeModel(b) || "Vehicle";

/** What else identifies the car under its title — make and plate
 * ("Honda City · MP09AB1234"); "" on a quick booking (type only). */
export function carIdentity(b: Booking, types?: CarTypes): string {
  const title = carTitle(b, types);
  const make = makeModel(b);
  return [make && make !== title ? make : "", plateOf(b)].filter(Boolean).join(" · ");
}

/** "Sedan · Honda City · MP09AB1234" — type first, then make and plate when known. */
export const carFull = (b: Booking, types?: CarTypes) => [carTitle(b, types), carIdentity(b, types)].filter(Boolean).join(" · ");

/** "Sedan · Star Wash" */
export const carService = (b: Booking, types?: CarTypes) => `${carTitle(b, types)} · ${serviceName(b)}`;

/** A visit on one line: "2 × Sedan · Star Wash", or "Sedan · Star Wash + SUV · Foam Wash". */
export function visitCarService(cars: Booking[], types?: CarTypes): string {
  const counts = new Map<string, number>();
  for (const b of cars) {
    const key = carService(b, types);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return Array.from(counts, ([key, n]) => (n > 1 ? `${n} × ${key}` : key)).join(" + ");
}

export function rupees(n?: number | null): string {
  return `₹${Math.round(n ?? 0).toLocaleString("en-IN")}`;
}

/** "2:15 PM" — IST, 12-hour. */
export const clock = (iso?: string | null) => formatClockIST(iso).toUpperCase();

/** "8:00 AM – 11:00 AM", with the day in front when it isn't today. */
export function whenLabel(b: Booking, words: { today: string; tomorrow: string }, withToday = false): string {
  const day = formatDay(b.scheduled_date);
  const slot = formatSlot(b.scheduled_slot);
  if (day === "Today") return withToday ? `${words.today} · ${slot}` : slot;
  return `${day === "Tomorrow" ? words.tomorrow : day} · ${slot}`;
}

export function addressLine(b: Booking): string {
  const a = b.address_snapshot;
  if (!a) return "";
  return [a.line1, a.landmark].filter(Boolean).join(", ");
}

export function mapsUrl(b: Booking): string | null {
  const a = b.address_snapshot;
  if (!a) return null;
  if (a.latitude != null && a.longitude != null) {
    return `https://www.google.com/maps/dir/?api=1&destination=${a.latitude},${a.longitude}`;
  }
  const q = [a.line1, a.city, a.pincode].filter(Boolean).join(", ");
  return q ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(q)}` : null;
}

const digits = (phone?: string | null) => String(phone ?? "").replace(/\D/g, "");
export const telUrl = (phone?: string | null) => (digits(phone) ? `tel:+${digits(phone).length === 10 ? "91" : ""}${digits(phone)}` : null);
export const waUrl = (phone?: string | null) => (digits(phone) ? `https://wa.me/${digits(phone).length === 10 ? "91" : ""}${digits(phone)}` : null);

/** The IST calendar day (YYYY-MM-DD) of an ISO instant. */
export const istDay = (iso?: string | null) =>
  iso ? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date(iso)) : "";

/** Monday of the current IST week, YYYY-MM-DD. */
export function weekStartIST(): string {
  const today = istDay(new Date().toISOString());
  const d = new Date(`${today}T12:00:00+05:30`);
  const back = (d.getUTCDay() + 6) % 7; // Mon=0 … Sun=6
  return istDay(new Date(d.getTime() - back * 86_400_000).toISOString());
}

export function istHour(): number {
  return Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", hourCycle: "h23" }).format(new Date()));
}

/** The earliest moment the backend lets him head out: 30 min before the
 * visit's own start (estimated_start_at, else the slot start). */
export function headOutOpensAt(cars: Booking[]): number | null {
  const times = cars
    .map((c) => {
      if (c.estimated_start_at) return new Date(c.estimated_start_at).getTime();
      const start = c.scheduled_slot?.split("-")[0]?.trim();
      return start ? new Date(`${c.scheduled_date.slice(0, 10)}T${start}:00+05:30`).getTime() : NaN;
    })
    .filter((n) => !Number.isNaN(n));
  if (!times.length) return null;
  return Math.min(...times) - 30 * 60_000;
}

/** On a visit, the car he works next: a picked one if still open, else the
 * first not finished, else the first. */
export function currentCar(slab: BookingSlab, pickedId?: string | null): Booking {
  const open = slab.bookings.filter((c) => !isFinished(c));
  return (pickedId && open.find((c) => c.id === pickedId)) || open[0] || slab.primary;
}

/** The "Next booking": whatever he's in the middle of, else the next job
 * he can actually act on (a missed-window job waiting on the manager never
 * sits on top), soonest first — the list arrives sorted by the server. */
export function pickNext(jobs: Booking[], excludeKey?: string | null): BookingSlab | null {
  const slabs = toSlabs(jobs).filter((s) => s.key !== excludeKey);
  const live = slabs.filter((s) => !needsManager(currentCar(s)));
  return (
    live.find((s) => ["captain_on_the_way", "service_started"].includes(currentCar(s).status)) ||
    live[0] ||
    null
  );
}

export const slabKeyOf = (b: Booking) => b.booking_group_id || b.id;

/** Money still owed on a visit, and how it may be taken. */
export function paymentOf(cars: Booking[]) {
  const live = cars.filter((c) => c.status !== "cancelled");
  const unpaid = live.filter((c) => c.payment_status !== "paid" && (c.total_amount ?? 0) > 0);
  const due = unpaid.reduce((s, c) => s + (c.total_amount ?? 0), 0);
  const total = live.reduce((s, c) => s + (c.total_amount ?? 0), 0);
  const prepaid = live.some((c) => c.prepaid_only);
  const plan = live.every((c) => c.payment_method === "subscription" || (c.total_amount ?? 0) === 0);
  const cash = live.some((c) => c.payment_method === "cash");
  // What he physically takes in cash for the visit: every cash car — a
  // car finished earlier on the visit already counts as paid (completion
  // settles it), but the customer hands over the money once, at the end.
  const cashTotal = live.filter((c) => c.payment_method === "cash").reduce((s, c) => s + (c.total_amount ?? 0), 0);
  return { due, total, prepaid, plan, cash, cashTotal, paid: unpaid.length === 0 };
}
