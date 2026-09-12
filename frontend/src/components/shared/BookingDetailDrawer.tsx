import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Calendar, Car, Clock, CreditCard, Flag, MapPin, Navigation, Star, User as UserIcon, Wrench } from "lucide-react";
import { Badge, Modal, StatusBadge } from "../ui";
import { reviewApi } from "../../api/engagement";
import { bookingApi, travelStatusApi } from "../../api/booking";
import { formatDateTime } from "../../lib/date";
import { ISSUE_LABELS, vehicleLabel } from "../../lib/constants";
import { combinedStatus } from "../../lib/bookingGroups";
import type { Booking } from "../../types";

/**
 * The complete detail view for ONE VISIT — everything a manager, captain,
 * or admin needs to understand what happened, in one place instead of
 * stitching it together across pages (Sections 7, 8, 13 of the BLUSSIT UX
 * update). A row in a booking list opens this; nothing else does — one
 * shared component instead of a slightly-different detail view per role.
 *
 * A visit can be several cars at one address in one slot. They are one job
 * to dispatch, so the drawer opens as one slab: the customer, the address
 * and the slot are stated once, every car is listed with its own service
 * and price, and the money is the visit's. The per-car work — who verified
 * which plate, whose photos, which timeline — stays per car, behind a
 * selector, because that's genuinely different for each one.
 */
export function BookingDetailDrawer({
  booking,
  onClose,
  captainName,
  centerName,
}: {
  booking: Booking | null;
  onClose: () => void;
  /** Resolved captain full name, if the caller already has a lookup —
   * falls back to showing nothing rather than a raw id. */
  captainName?: string | null;
  centerName?: string | null;
}) {
  // The rest of the visit, when the row opened is one car of several.
  const groupId = booking?.booking_group_id || null;
  const { data: visitBookings } = useQuery({
    queryKey: ["booking-group", groupId],
    queryFn: () => bookingApi.getGroup(groupId as string),
    enabled: !!groupId,
  });
  const cars: Booking[] = groupId && visitBookings?.length ? visitBookings : booking ? [booking] : [];
  const isVisit = cars.length > 1;
  const visitTotal = cars.reduce((sum, c) => sum + (c.total_amount || 0), 0);

  // Which car's work is on show. Defaults to the one whose row was
  // clicked, and resets whenever a different booking opens the drawer.
  const [carId, setCarId] = useState<string | null>(null);
  useEffect(() => setCarId(booking?.id || null), [booking?.id]);
  const car = cars.find((c) => c.id === carId) || booking;
  const flagged = cars.filter((c) => c.issue_flag && !c.issue_resolved);
  const paymentPending = cars.some((c) => c.payment_status !== "paid");
  /** "Car 2 · MP09RB0002" — how a per-car section is labelled on a visit. */
  const carTag = (c: Booking) =>
    `Car ${cars.indexOf(c) + 1} · ${c.vehicle_snapshot?.registration_number || c.vehicle_registration_number || c.booking_number}`;

  const travelActive = car != null && ["assigned", "captain_on_the_way"].includes(car.status);
  const { data: travel } = useQuery({
    queryKey: ["travel-status", car?.id],
    queryFn: () => travelStatusApi.get(car!.id),
    enabled: car != null,
    refetchInterval: travelActive ? 45000 : false,
  });

  const { data: review } = useQuery({
    queryKey: ["booking-review", car?.id],
    queryFn: () => reviewApi.forBooking(car!.id),
    enabled: !!car,
  });

  return (
    <Modal
      open={!!booking}
      onClose={onClose}
      title={booking ? (isVisit ? `${booking.booking_number} · ${cars.length} vehicles` : booking.booking_number) : "Booking"}
      maxWidth="max-w-2xl"
    >
      {booking && car && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            {/* The VISIT's status: least-advanced car wins, because the
                visit isn't done until the last car is. */}
            <StatusBadge status={isVisit ? combinedStatus(cars) : car.status} />
            <PriorityBadge priority={booking.priority} />
            {isVisit && (
              <Badge tone="neutral">
                <Car className="h-3 w-3" /> 1 visit · {cars.length} vehicles
              </Badge>
            )}
            {booking.source === "whatsapp" && <Badge tone="success">Booked via WhatsApp</Badge>}
            {booking.source === "staff" && <Badge tone="neutral">Booked by staff</Badge>}
            {flagged.map((f) => (
              <Badge key={f.id} tone="error">
                <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[f.issue_flag!] || f.issue_flag}
                {isVisit ? ` · ${f.booking_number}` : ""}
              </Badge>
            ))}
          </div>

          {flagged
            .filter((f) => f.issue_notes)
            .map((f) => (
              <div key={f.id} className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2.5 text-sm text-[var(--color-text-primary)]">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" />
                <span>
                  {isVisit && <span className="font-mono-num mr-1.5 font-semibold">{f.booking_number}</span>}
                  {f.issue_notes}
                </span>
              </div>
            ))}

          <Section title={isVisit ? "Customer & vehicles" : "Customer & vehicle"} icon={UserIcon}>
            <Row label="Customer" value={booking.customer_name} />
            {booking.customer_phone && (
              <Row label="Phone" value={<a href={`tel:${booking.customer_phone}`} className="hover:text-[var(--color-primary)]">{booking.customer_phone}</a>} />
            )}
            {/* Every car on the visit, each with what it's having done and
                what it costs — the thing a one-vehicle view can't show. */}
            {isVisit ? (
              <div className="mt-1 space-y-2 border-t border-gray-100 pt-2">
                {cars.map((c, i) => (
                  <div key={c.id} className="flex items-start justify-between gap-3 text-sm">
                    <span className="min-w-0">
                      <span className="block font-medium text-[var(--color-text-primary)]">
                        <span className="text-[var(--color-text-secondary)]">{i + 1}.</span> {vehicleLabel(c) || c.vehicle_registration_number}
                      </span>
                      <span className="block text-xs text-[var(--color-text-secondary)]">
                        {c.combo_name || c.service_names?.join(", ") || "Service"}
                        <span className="font-mono-num ml-1.5 text-gray-400">{c.booking_number}</span>
                      </span>
                    </span>
                    <span className="font-mono-num shrink-0 font-medium text-[var(--color-text-primary)]">₹{c.total_amount}</span>
                  </div>
                ))}
              </div>
            ) : (
              <Row
                label="Vehicle"
                value={booking.vehicle_snapshot ? `${booking.vehicle_snapshot.brand} ${booking.vehicle_snapshot.model} · ${booking.vehicle_snapshot.registration_number}` : booking.vehicle_registration_number}
              />
            )}
          </Section>

          <Section title="Service & location" icon={Wrench}>
            {!isVisit && <Row label="Service" value={booking.combo_name || booking.service_names?.join(", ")} />}
            <Row label="Service center" value={centerName} />
            <Row
              label="Slot"
              value={
                <span className="font-mono-num">
                  {new Date(booking.scheduled_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · {booking.scheduled_slot}
                </span>
              }
            />
            {booking.address_snapshot && (
              <Row
                label="Address"
                value={
                  <span className="flex items-start gap-1">
                    <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-secondary)]" />
                    {[booking.address_snapshot.line1, booking.address_snapshot.landmark, booking.address_snapshot.city, booking.address_snapshot.pincode].filter(Boolean).join(", ")}
                    {booking.address_snapshot.latitude != null && booking.address_snapshot.longitude != null && (
                      <a
                        href={`https://www.google.com/maps/dir/?api=1&destination=${booking.address_snapshot.latitude},${booking.address_snapshot.longitude}`}
                        target="_blank"
                        rel="noreferrer"
                        className="ml-2 text-xs font-semibold text-[var(--color-primary)] underline"
                      >
                        Open in Maps
                      </a>
                    )}
                  </span>
                }
              />
            )}
            {travel?.store_to_customer && (
              <Row
                label="From center"
                value={
                  <span className="font-mono-num">
                    {travel.store_to_customer.km} km
                    {travel.store_to_customer.minutes != null ? ` · ~${travel.store_to_customer.minutes} min` : ""}
                  </span>
                }
              />
            )}
            {travel?.captain_to_customer && (
              <div className="mt-2 flex items-center gap-2 rounded-xl bg-[var(--color-primary-light)] px-3 py-2.5">
                <Navigation className="h-4 w-4 shrink-0 text-[var(--color-primary)] animate-pulse" />
                <p className="text-sm text-[var(--color-text-primary)]">
                  <span className="font-semibold">{travel.captain_to_customer.captain_name || "Your captain"}</span> is{" "}
                  {travel.captain_to_customer.minutes != null ? (
                    <>about <span className="font-mono-num font-bold">{travel.captain_to_customer.minutes} min</span> away</>
                  ) : (
                    <><span className="font-mono-num font-bold">{travel.captain_to_customer.km} km</span> away</>
                  )}
                  {travel.captain_to_customer.minutes != null && (
                    <span className="text-[var(--color-text-secondary)]"> · {travel.captain_to_customer.km} km</span>
                  )}
                </p>
              </div>
            )}
            <Row label="Captain" value={captainName || (car.captain_id ? "Assigned" : "Not yet assigned")} />
          </Section>

          <Section title="Payment" icon={CreditCard}>
            {/* One visit is one bill. The per-car lines are still shown,
                because a captain collecting cash needs the total, and the
                office reconciling it needs the split. */}
            {isVisit &&
              cars.map((c) => (
                <Row
                  key={c.id}
                  label={vehicleLabel(c) || c.booking_number}
                  value={<span className="font-mono-num">₹{c.total_amount}</span>}
                />
              ))}
            <Row
              label={isVisit ? `Total · ${cars.length} vehicles` : "Amount"}
              value={<span className="font-mono-num">₹{visitTotal}</span>}
            />
            <Row label="Method" value={<span className="capitalize">{booking.payment_method?.replace(/_/g, " ")}</span>} />
            <Row
              label="Status"
              value={<Badge tone={paymentPending ? "neutral" : "success"}>{paymentPending ? "pending" : "paid"}</Badge>}
            />
          </Section>

          {/* Below this line everything is about ONE car: its own plate
              check, its own photos, its own clock. A visit has several, so
              the manager picks which one to look at rather than being shown
              a merged timeline that describes no actual car. */}
          {isVisit && (
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
                <Car className="h-3.5 w-3.5" /> Work on each vehicle
              </p>
              <div className="flex flex-wrap gap-2">
                {cars.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setCarId(c.id)}
                    className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                      c.id === car.id
                        ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-text-primary)]"
                        : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                    }`}
                  >
                    {carTag(c)}
                    <span className="ml-1.5 capitalize text-[var(--color-text-secondary)]">{c.status.replace(/_/g, " ")}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <Section title={isVisit ? `Timeline · ${carTag(car)}` : "Timeline"} icon={Clock}>
            <TimelineRow label="Booking created" value={car.created_at} />
            <TimelineRow label="Manager notified" value={car.manager_notified_at} />
            <TimelineRow label="Captain assigned" value={car.assigned_at} />
            <TimelineRow label="Captain heading out" value={car.heading_at} />
            <TimelineRow label="Vehicle verified (arrived)" value={car.vehicle_verified_at} />
            <TimelineRow label="Service started" value={car.service_started_at} />
            <TimelineRow label="Service completed" value={car.completed_at} />
            <TimelineRow label="Closed" value={car.closed_at} />
            {car.delay_minutes != null && car.delay_minutes > 0 && (
              <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--color-error)]">
                <AlertTriangle className="h-3.5 w-3.5" /> {car.delay_minutes} min over expected duration
              </p>
            )}
          </Section>

          {/* Every workflow tap's captured GPS, each one openable in
              Google Maps — when a geofence flag says "captain was 900m
              from the customer", THIS is where the manager sees exactly
              where that tap happened, so there's evidence to put in
              front of the captain instead of just a distance number. */}
          {(car.heading_location || car.arrival_location || car.before_photo || car.after_photo) && (
            <Section title={isVisit ? `Location checks · ${carTag(car)}` : "Location checks"} icon={MapPin}>
              <LocationCheckRow label="Started heading from" point={car.heading_location} at={car.heading_at} />
              <LocationCheckRow
                label={'"I\'ve reached" tapped at'}
                point={car.arrival_location}
                at={car.vehicle_verified_at}
                flagged={car.arrival_flagged}
                distanceM={car.arrival_distance_m}
              />
              <LocationCheckRow
                label="Before-photo taken at"
                point={car.before_photo}
                at={car.before_photo?.captured_at}
                flagged={car.before_photo_flagged}
                distanceM={car.before_photo_distance_m}
              />
              <LocationCheckRow
                label="After-photo taken at"
                point={car.after_photo}
                at={car.after_photo?.captured_at}
                flagged={car.after_photo_flagged}
                distanceM={car.after_photo_distance_m}
              />
            </Section>
          )}

          {(car.before_photo || car.after_photo) && (
            <Section title={isVisit ? `Photos · ${carTag(car)}` : "Photos"} icon={Calendar}>
              <div className="grid grid-cols-2 gap-3">
                {car.before_photo && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Before{car.before_photo_flagged ? " (flagged — location mismatch)" : ""}</p>
                    <img src={car.before_photo.image_url} alt="Before service" className="aspect-square w-full rounded-lg object-cover" />
                  </div>
                )}
                {car.after_photo && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">After{car.after_photo_flagged ? " (flagged — location mismatch)" : ""}</p>
                    <img src={car.after_photo.image_url} alt="After service" className="aspect-square w-full rounded-lg object-cover" />
                  </div>
                )}
              </div>
            </Section>
          )}

          <Section title={isVisit ? `Review · ${carTag(car)}` : "Review"} icon={Star}>
            {review ? (
              <div className="space-y-2">
                <StarRow label="Captain" rating={review.captain_rating ?? review.rating ?? 0} />
                <StarRow label="Service" rating={review.service_rating ?? review.rating ?? 0} />
                {(review.captain_comment || review.service_comment || review.comment) && (
                  <p className="text-sm text-[var(--color-text-primary)]">{review.captain_comment || review.service_comment || review.comment}</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-[var(--color-text-secondary)]">No review yet</p>
            )}
          </Section>
        </div>
      )}
    </Modal>
  );
}

function Section({ title, icon: Icon, children }: { title: string; icon: typeof UserIcon; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
        <Icon className="h-3.5 w-3.5" /> {title}
      </p>
      <div className="space-y-1.5 rounded-xl border border-gray-100 p-3">{children}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex items-start justify-between gap-4 text-sm">
      <span className="shrink-0 text-[var(--color-text-secondary)]">{label}</span>
      <span className="text-right font-medium text-[var(--color-text-primary)]">{value}</span>
    </div>
  );
}

function LocationCheckRow({
  label,
  point,
  at,
  flagged,
  distanceM,
}: {
  label: string;
  point?: { latitude: number; longitude: number } | null;
  at?: string | null;
  flagged?: boolean;
  distanceM?: number | null;
}) {
  if (!point || point.latitude == null || point.longitude == null) return null;
  return (
    <div className={`flex flex-wrap items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm ${flagged ? "bg-amber-50" : ""}`}>
      <span className="min-w-0">
        <span className={flagged ? "font-semibold text-amber-800" : "text-[var(--color-text-secondary)]"}>{label}</span>
        {at && <span className="ml-1.5 font-mono-num text-xs text-gray-400">{formatDateTime(at)}</span>}
        {distanceM != null && (
          <span className={`ml-1.5 text-xs ${flagged ? "font-bold text-amber-700" : "text-gray-400"}`}>
            {Math.round(distanceM)}m from the customer's address{flagged ? " — flagged" : ""}
          </span>
        )}
      </span>
      <a
        href={`https://www.google.com/maps?q=${point.latitude},${point.longitude}`}
        target="_blank"
        rel="noreferrer"
        className="flex shrink-0 items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs font-semibold text-[var(--color-text-primary)] hover:border-black"
      >
        <MapPin className="h-3 w-3" /> Open in Maps
      </a>
    </div>
  );
}

function TimelineRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span className="text-[var(--color-text-secondary)]">{label}</span>
      <span className={`font-mono-num text-xs ${value ? "text-[var(--color-text-primary)]" : "text-gray-300"}`}>{value ? formatDateTime(value) : "—"}</span>
    </div>
  );
}

function StarRow({ label, rating }: { label: string; rating: number }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-14 shrink-0 text-[var(--color-text-secondary)]">{label}</span>
      <div className="flex items-center gap-0.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Star key={i} className={`h-3.5 w-3.5 ${i < rating ? "fill-[var(--color-secondary)] text-[var(--color-secondary)]" : "text-gray-200"}`} />
        ))}
      </div>
      <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{rating.toFixed(1)}</span>
    </div>
  );
}

function PriorityBadge({ priority }: { priority: Booking["priority"] }) {
  if (priority === "high") {
    return (
      <Badge tone="error">
        <Flag className="h-3 w-3" /> High priority
      </Badge>
    );
  }
  if (priority === "low") return <Badge tone="neutral">Low priority</Badge>;
  return <Badge tone="neutral">Medium priority</Badge>;
}
