import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Calendar, Clock, CreditCard, Flag, MapPin, Star, User as UserIcon, Wrench } from "lucide-react";
import { Badge, Modal, StatusBadge } from "../ui";
import { reviewApi } from "../../api/engagement";
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
                  </span>
                }
              />
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
