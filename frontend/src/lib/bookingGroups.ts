import type { Booking } from "../types";
import { vehicleLabel } from "./constants";

/**
 * Several cars washed on ONE visit are several bookings underneath — each
 * needs its own plate check, photos and proof of work — but to everyone
 * LOOKING at them they are one job at one address in one slot. Showing them
 * as separate rows makes a customer think they booked twice and makes a
 * manager think there are two jobs to dispatch.
 *
 * This collapses a flat booking list into "slabs": a single booking stays
 * exactly as it was, a visit becomes one row that knows about its cars.
 */
export interface BookingSlab {
  key: string;
  /** Every car on this slab, in the order they'll be worked. */
  bookings: Booking[];
  /** The car that carries the slab's identity — first on the visit. */
  primary: Booking;
  /** More than one car: render the combined wording. */
  isVisit: boolean;
  vehicleCount: number;
  totalAmount: number;
  /** "Waterless Service + Deep Cleaning" */
  serviceLabel: string;
  /** "Mahindra Thar · MP09XC9455 + Tata Harrier · MP09AB1234" */
  vehicleLabel: string;
  /** The visit's own status — see `combinedStatus`. */
  status: string;
  /** Unpaid if ANY car on the visit still owes money. */
  paymentPending: boolean;
}

// Least-advanced wins when a visit's cars differ: the visit isn't started
// until the first car starts and isn't finished until the last one is.
const LIFECYCLE = [
  "awaiting_payment",
  "pending",
  "rescheduled",
  "assigned",
  "captain_on_the_way",
  "service_started",
  "completed",
];

export function combinedStatus(bookings: Booking[]): string {
  const live = bookings.filter((b) => b.status !== "cancelled");
  if (!live.length) return "cancelled";
  return live.reduce((lowest, b) => {
    const a = LIFECYCLE.indexOf(lowest);
    const c = LIFECYCLE.indexOf(b.status);
    if (a === -1) return b.status;
    if (c === -1) return lowest;
    return c < a ? b.status : lowest;
  }, live[0].status);
}

const serviceOf = (b: Booking) => b.combo_name || b.service_names?.join(", ") || "Service";

/** Group a flat booking list into visits, preserving the incoming order. */
export function toSlabs(bookings: Booking[]): BookingSlab[] {
  const slabs: BookingSlab[] = [];
  const byGroup = new Map<string, BookingSlab>();

  for (const booking of bookings) {
    const groupId = booking.booking_group_id;
    if (!groupId) {
      slabs.push(buildSlab(booking.id, [booking]));
      continue;
    }
    const existing = byGroup.get(groupId);
    if (existing) {
      existing.bookings.push(booking);
      continue;
    }
    const slab = buildSlab(groupId, [booking]);
    byGroup.set(groupId, slab);
    slabs.push(slab);
  }

  // Recompute once every car of each visit has been collected.
  for (const slab of slabs) {
    if (slab.bookings.length === 1) continue;
    Object.assign(slab, buildSlab(slab.key, slab.bookings));
  }
  return slabs;
}

function buildSlab(key: string, bookings: Booking[]): BookingSlab {
  const ordered = [...bookings].sort(
    (a, b) => (a.group_offset_minutes ?? 0) - (b.group_offset_minutes ?? 0)
  );
  const primary = ordered[0];
  return {
    key,
    bookings: ordered,
    primary,
    isVisit: ordered.length > 1,
    vehicleCount: ordered.length,
    totalAmount: ordered.reduce((sum, b) => sum + (b.total_amount || 0), 0),
    serviceLabel: ordered.map(serviceOf).join(" + "),
    vehicleLabel: ordered.map(vehicleLabel).filter(Boolean).join(" + "),
    status: combinedStatus(ordered),
    paymentPending: ordered.some((b) => b.payment_status === "pending" && b.status !== "cancelled"),
  };
}
