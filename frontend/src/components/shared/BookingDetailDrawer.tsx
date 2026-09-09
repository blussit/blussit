import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Calendar, Clock, CreditCard, Flag, MapPin, Navigation, Star, User as UserIcon, Wrench } from "lucide-react";
import { Badge, Modal, StatusBadge } from "../ui";
import { reviewApi } from "../../api/engagement";
import { travelStatusApi } from "../../api/booking";
import { formatDateTime } from "../../lib/date";
import { ISSUE_LABELS } from "../../lib/constants";
import type { Booking } from "../../types";

/**
 * The complete detail view for ONE booking — everything a manager,
 * captain, or admin needs to understand what happened, in one place
 * instead of stitching it together across pages (Sections 7, 8, 13 of
 * the BLUSSIT UX update). A row in a booking list opens this; nothing
 * else does — one shared component instead of a slightly-different
 * detail view per role.
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
  const travelActive = booking != null && ["assigned", "captain_on_the_way"].includes(booking.status);
  const { data: travel } = useQuery({
    queryKey: ["travel-status", booking?.id],
    queryFn: () => travelStatusApi.get(booking!.id),
    enabled: booking != null,
    refetchInterval: travelActive ? 45000 : false,
  });

  const { data: review } = useQuery({
    queryKey: ["booking-review", booking?.id],
    queryFn: () => reviewApi.forBooking(booking!.id),
    enabled: !!booking,
  });

  return (
    <Modal open={!!booking} onClose={onClose} title={booking ? booking.booking_number : "Booking"} maxWidth="max-w-2xl">
      {booking && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={booking.status} />
            <PriorityBadge priority={booking.priority} />
            {booking.source === "whatsapp" && <Badge tone="success">Booked via WhatsApp</Badge>}
            {booking.source === "staff" && <Badge tone="neutral">Booked by staff</Badge>}
            {booking.issue_flag && !booking.issue_resolved && (
              <Badge tone="error">
                <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[booking.issue_flag] || booking.issue_flag}
              </Badge>
            )}
          </div>

          {booking.issue_flag && !booking.issue_resolved && booking.issue_notes && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2.5 text-sm text-[var(--color-text-primary)]">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" />
              {booking.issue_notes}
            </div>
          )}

          <Section title="Customer & vehicle" icon={UserIcon}>
            <Row label="Customer" value={booking.customer_name} />
            {booking.customer_phone && (
              <Row label="Phone" value={<a href={`tel:${booking.customer_phone}`} className="hover:text-[var(--color-primary)]">{booking.customer_phone}</a>} />
            )}
            <Row
              label="Vehicle"
              value={booking.vehicle_snapshot ? `${booking.vehicle_snapshot.brand} ${booking.vehicle_snapshot.model} · ${booking.vehicle_snapshot.registration_number}` : booking.vehicle_registration_number}
            />
          </Section>

          <Section title="Service & location" icon={Wrench}>
            <Row label="Service" value={booking.combo_name || booking.service_names?.join(", ")} />
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
            <Row label="Captain" value={captainName || (booking.captain_id ? "Assigned" : "Not yet assigned")} />
          </Section>

          <Section title="Payment" icon={CreditCard}>
            <Row label="Amount" value={<span className="font-mono-num">₹{booking.total_amount}</span>} />
            <Row label="Method" value={<span className="capitalize">{booking.payment_method?.replace(/_/g, " ")}</span>} />
            <Row label="Status" value={<Badge tone={booking.payment_status === "paid" ? "success" : "neutral"}>{booking.payment_status}</Badge>} />
          </Section>

          <Section title="Timeline" icon={Clock}>
            <TimelineRow label="Booking created" value={booking.created_at} />
            <TimelineRow label="Manager notified" value={booking.manager_notified_at} />
            <TimelineRow label="Captain assigned" value={booking.assigned_at} />
            <TimelineRow label="Captain heading out" value={booking.heading_at} />
            <TimelineRow label="Vehicle verified (arrived)" value={booking.vehicle_verified_at} />
            <TimelineRow label="Service started" value={booking.service_started_at} />
            <TimelineRow label="Service completed" value={booking.completed_at} />
            <TimelineRow label="Closed" value={booking.closed_at} />
            {booking.delay_minutes != null && booking.delay_minutes > 0 && (
              <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--color-error)]">
                <AlertTriangle className="h-3.5 w-3.5" /> {booking.delay_minutes} min over expected duration
              </p>
            )}
          </Section>

          {/* Every workflow tap's captured GPS, each one openable in
              Google Maps — when a geofence flag says "captain was 900m
              from the customer", THIS is where the manager sees exactly
              where that tap happened, so there's evidence to put in
              front of the captain instead of just a distance number. */}
          {(booking.heading_location || booking.arrival_location || booking.before_photo || booking.after_photo) && (
            <Section title="Location checks" icon={MapPin}>
              <LocationCheckRow label="Started heading from" point={booking.heading_location} at={booking.heading_at} />
              <LocationCheckRow
                label={'"I\'ve reached" tapped at'}
                point={booking.arrival_location}
                at={booking.vehicle_verified_at}
                flagged={booking.arrival_flagged}
                distanceM={booking.arrival_distance_m}
              />
              <LocationCheckRow
                label="Before-photo taken at"
                point={booking.before_photo}
                at={booking.before_photo?.captured_at}
                flagged={booking.before_photo_flagged}
                distanceM={booking.before_photo_distance_m}
              />
              <LocationCheckRow
                label="After-photo taken at"
                point={booking.after_photo}
                at={booking.after_photo?.captured_at}
                flagged={booking.after_photo_flagged}
                distanceM={booking.after_photo_distance_m}
              />
            </Section>
          )}

          {(booking.before_photo || booking.after_photo) && (
            <Section title="Photos" icon={Calendar}>
              <div className="grid grid-cols-2 gap-3">
                {booking.before_photo && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Before{booking.before_photo_flagged ? " (flagged — location mismatch)" : ""}</p>
                    <img src={booking.before_photo.image_url} alt="Before service" className="aspect-square w-full rounded-lg object-cover" />
                  </div>
                )}
                {booking.after_photo && (
                  <div>
                    <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">After{booking.after_photo_flagged ? " (flagged — location mismatch)" : ""}</p>
                    <img src={booking.after_photo.image_url} alt="After service" className="aspect-square w-full rounded-lg object-cover" />
                  </div>
                )}
              </div>
            </Section>
          )}

          <Section title="Review" icon={Star}>
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
