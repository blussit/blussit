import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Calendar, Car, Clock, CreditCard, Flag, HandCoins, LockOpen, MapPin, Navigation, Pencil, Plus, Star, Trash2, User as UserIcon, WifiOff, Wrench } from "lucide-react";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { useAuth } from "../../context/AuthContext";
import { TipModal } from "./TipModal";
import { AddedOnSiteLines, AddServiceDialog, CustomerEditedChip, HistoryChanges, PAYMENT_STATUS_LABELS, paymentTone } from "./BookingStaffExtras";
import { moneyOf, staffBookingApi, TIP_METHOD_LABELS, type ManagerPayback, type StaffBooking, type TipMethod } from "../../api/staffBookings";
import { PAYBACK_REASON_LABELS, PAYOUT_METHOD_LABELS, type PaybackReason, type PayoutMethod } from "../../api/customerWallet";
import { PaybackDialog, paybackOption } from "./PaybackDialog";
import { Badge, Button, ErrorState, Modal, StatusBadge } from "../ui";
import { reviewApi } from "../../api/engagement";
import { bookingApi, travelStatusApi } from "../../api/booking";
import { asUtcInstant, formatDateTime, formatSlot } from "../../lib/date";
import { isArrivalLocked, issueLabel, vehicleLabel } from "../../lib/constants";
import { bookingServiceLabel, combinedStatus } from "../../lib/bookingGroups";
import { toTitle } from "../../lib/titleCase";
import type { Booking } from "../../types";

/** "Sedan" (quick booking) or "Sedan · Honda City · MP09AB1234" (older
 *  saved vehicle) — staff always see the car TYPE first. */
function carLine(c: Booking): string {
  const type = toTitle(c.vehicle_type_name);
  const label = vehicleLabel(c);
  if (!type) return label;
  if (label === "Vehicle" || label.toLowerCase() === type.toLowerCase()) return type;
  return label.toLowerCase().startsWith(type.toLowerCase()) ? label : `${type} · ${label}`;
}

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
  onEdit,
  onDelete,
  onCancel,
  hasComplaint = false,
}: {
  booking: Booking | null;
  onClose: () => void;
  /** Resolved captain full name, if the caller already has a lookup —
   * falls back to showing nothing rather than a raw id. */
  captainName?: string | null;
  centerName?: string | null;
  /** Admin-only actions; omitted by every other caller, so no buttons show.
   *  onEdit gets a car on the visit that is still open (edit fans out to
   *  the whole visit server-side). */
  onEdit?: (booking: Booking) => void;
  onDelete?: (booking: Booking) => void;
  /** Staff cancel (opens the caller's StaffCancelDialog) — gets a car on
   *  the visit that is still open; the whole visit is cancelled. */
  onCancel?: (booking: Booking) => void;
  /** Opened from a complaint: a payback for it is allowed (the server checks). */
  hasComplaint?: boolean;
}) {
  // The rest of the visit, when the row opened is one car of several.
  const groupId = booking?.booking_group_id || null;
  const visitQuery = useQuery({
    queryKey: ["booking-group", groupId],
    queryFn: () => bookingApi.getGroup(groupId as string),
    enabled: !!groupId,
  });
  const visitBookings = visitQuery.data;
  // Without the group read a multi-car visit would show as one car.
  const visitFailed = !!groupId && visitQuery.isError && !visitBookings;
  // A single booking's list row has no status_history (and may be stale on
  // money): staff re-read it. A visit's group read already carries both.
  const { user: me } = useAuth();
  const staffViewer = me?.role === "manager" || me?.role === "admin";
  const freshQuery = useQuery({
    queryKey: ["staff-booking", booking?.id],
    queryFn: () => staffBookingApi.get(booking!.id),
    enabled: !!booking && !groupId && staffViewer,
  });
  const single: Booking | null = booking ? ({ ...booking, ...(freshQuery.data && freshQuery.data.id === booking.id ? freshQuery.data : {}) } as Booking) : null;
  const cars: StaffBooking[] = groupId && visitBookings?.length ? visitBookings : single ? [single] : [];
  const isVisit = cars.length > 1;
  const visitTotal = cars.reduce((sum, c) => sum + (c.total_amount || 0), 0);

  // Which car's work is on show. Defaults to the one whose row was
  // clicked, and resets whenever a different booking opens the drawer.
  const [carId, setCarId] = useState<string | null>(null);
  useEffect(() => setCarId(booking?.id || null), [booking?.id]);
  const car: StaffBooking | null = cars.find((c) => c.id === carId) || single;
  const [addFor, setAddFor] = useState<StaffBooking | null>(null);
  // Money of the visit: wallet used, previous balance carried, paid, due.
  const money = cars.reduce(
    (acc, c) => {
      const m = moneyOf(c);
      return { wallet: acc.wallet + m.wallet, paid: acc.paid + m.paid, due: acc.due + m.due, carried: acc.carried + m.carried };
    },
    { wallet: 0, paid: 0, due: 0, carried: 0 },
  );
  const visitPayStatus = cars.every((c) => c.payment_status === "paid")
    ? "paid"
    : cars.some((c) => c.payment_status === "partially_paid") || (money.paid > 0 && money.due > 0)
      ? "partially_paid"
      : "pending";
  const editedCars = cars.filter((c) => c.customer_edited_at);
  const flagged = cars.filter((c) => c.issue_flag && !c.issue_resolved);
  const paymentPending = cars.some((c) => c.payment_status !== "paid");
  const editableCar = cars.find((c) => !["completed", "cancelled"].includes(c.status)) || null;
  const prepaid = cars.some((c) => c.prepaid_only);
  // Charged once per visit (on its first car); summed so it shows whichever car carries it.
  const travelCharge = cars.reduce((sum, c) => sum + (c.travel_charge || 0), 0);
  const travelKm = cars.find((c) => (c.travel_charge || 0) > 0)?.travel_charge_km;
  // The visit's tip (kept on one of its cars, already inside its total) —
  // only on jobs the manager did, only shown to managers/admins. `tipSaved`
  // shows a fresh save straight away.
  const { user } = useAuth();
  const [tipOpen, setTipOpen] = useState(false);
  const [tipSaved, setTipSaved] = useState<{ tip: number; method: TipMethod } | null>(null);
  useEffect(() => setTipSaved(null), [booking?.id]);
  const visitTip = tipSaved?.tip ?? cars.reduce((sum, c) => sum + (Number(c.tip_amount) || 0), 0);
  const doneCar = cars.find((c) => c.status === "completed" && c.completed_by_role === "manager") || null;
  const canEditTip = !!doneCar && (user?.role === "manager" || user?.role === "admin");
  const showTip = canEditTip || (visitTip > 0 && (user?.role === "manager" || user?.role === "admin"));
  /** "Car 2 · MP09RB0002" — how a per-car section is labelled on a visit. */
  const carTag = (c: Booking) =>
    `Car ${cars.indexOf(c) + 1} · ${carLine(c) || c.booking_number}`;

  const travelActive = car != null && ["assigned", "captain_on_the_way"].includes(car.status);
  const { data: travel } = useQuery({
    queryKey: ["travel-status", car?.id],
    queryFn: () => travelStatusApi.get(car!.id),
    enabled: car != null,
    refetchInterval: travelActive ? 45000 : false,
  });

  const reviewQuery = useQuery({
    queryKey: ["booking-review", car?.id],
    queryFn: () => reviewApi.forBooking(car!.id),
    enabled: !!car,
  });
  const review = reviewQuery.data;

  // Money staff see the same way everywhere: a previous late-cancellation
  // charge, the manager's discount and the tip (with who and when), and a
  // refund on a paid-then-cancelled booking.
  const isStaff = user?.role === "manager" || user?.role === "admin";
  const cancellationCharge = cars.reduce((sum, c) => sum + (Number(c.cancellation_charge) || 0), 0);
  const discountCar = cars.find((c) => (Number(c.manager_discount) || 0) > 0) || null;
  const managerDiscount = cars.reduce((sum, c) => sum + (Number(c.manager_discount) || 0), 0);
  const tipCar = cars.find((c) => (Number(c.tip_amount) || 0) > 0) || null;
  // A tip carries its own method (MONEY-2); one saved before that is cash.
  const tipMethod: TipMethod = tipSaved?.method ?? (tipCar?.tip_method === "online" ? "online" : "cash");
  // Money a manager paid back for this visit (MONEY-2), newest last.
  const paybacks: (ManagerPayback & { carNumber?: string })[] = cars.flatMap((c) =>
    (c.manager_paybacks || []).map((p) => ({ ...p, carNumber: isVisit ? c.booking_number : undefined })),
  );
  const paidBackTotal = cars.reduce((sum, c) => sum + (Number(c.paid_back_total) || 0), 0);
  // Pay Back To Customer: only a cancelled / delayed / flagged / complained
  // booking (the server decides; this just hides it where it can't apply).
  const paybackChoices = cars.map((c) => paybackOption(c, hasComplaint)).filter((o) => o.signals.length > 0);
  const [paybackOpen, setPaybackOpen] = useState(false);
  const refundCars = cars.filter((c) => c.payment_status === "refund_due" || c.payment_status === "refunded");
  const refundAmount = refundCars.reduce((sum, c) => sum + (Number(c.refunded_amount) || c.total_amount || 0), 0);
  const refundDone = refundCars.length > 0 && refundCars.every((c) => c.payment_status === "refunded");
  const refundedAt = refundCars.map((c) => c.refunded_at).filter(Boolean).sort().pop() || null;
  const lockedCar = cars.find(isArrivalLocked) || null;
  const noGps = cars.some((c) => c.arrival_no_gps);

  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const unlock = useMutation({
    mutationFn: (id: string) => bookingApi.unlockArrivalCode(id),
    onSuccess: () => {
      queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === "string" && /^(center-bookings|admin-center-bookings|admin-bookings|booking|manager-dashboard)/.test(q.queryKey[0]),
      });
      pushToast({ tone: "success", title: "Arrival Check Unlocked", message: "The captain can enter the code again." });
      onClose();
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Unlock", message: getErrorMessage(err) }),
  });

  return (
    <Modal
      open={!!booking}
      onClose={onClose}
      title={booking ? (isVisit ? `${booking.booking_number} · ${cars.length} Vehicles` : booking.booking_number) : "Booking"}
      maxWidth="max-w-2xl"
    >
      {booking && car && (
        <div className="space-y-5">
          {(onEdit || onDelete || (onCancel && editableCar) || (isStaff && lockedCar) || (isStaff && car.status !== "cancelled") || (isStaff && paybackChoices.length > 0)) && (
            <div className="flex flex-wrap justify-end gap-2 border-b border-gray-100 pb-3">
              {isStaff && paybackChoices.length > 0 && booking.customer_id && (
                <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => setPaybackOpen(true)} data-testid="drawer-payback">
                  <HandCoins className="h-3.5 w-3.5" /> Pay Back To Customer
                </Button>
              )}
              {isStaff && car.status !== "cancelled" && (
                <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => setAddFor(car)}>
                  <Plus className="h-3.5 w-3.5" /> Add Service{isVisit ? ` · Car ${cars.indexOf(car) + 1}` : ""}
                </Button>
              )}
              {isStaff && lockedCar && (
                <Button size="sm" isLoading={unlock.isPending} onClick={() => unlock.mutate(lockedCar.id)}>
                  <LockOpen className="h-3.5 w-3.5" /> Unlock Arrival Check
                </Button>
              )}
              {onCancel && editableCar && (
                <Button size="sm" variant="outline" onClick={() => onCancel(editableCar)}>
                  <Ban className="h-3.5 w-3.5 text-[var(--color-error)]" /> {isVisit ? "Cancel Visit" : "Cancel Booking"}
                </Button>
              )}
              {onEdit && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!editableCar}
                  title={editableCar ? undefined : "A completed or cancelled booking can't be edited"}
                  onClick={() => editableCar && onEdit(editableCar)}
                >
                  <Pencil className="h-3.5 w-3.5" /> Edit Details
                </Button>
              )}
              {onDelete && (
                <Button size="sm" variant="outline" onClick={() => onDelete(booking)}>
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" /> Delete
                </Button>
              )}
            </div>
          )}
          {visitFailed && (
            <p role="alert" className="text-xs text-[var(--color-text-secondary)]">
              Couldn't load the other cars on this visit.{" "}
              <button
                type="button"
                className="font-semibold text-[var(--color-primary)] hover:underline disabled:opacity-60"
                disabled={visitQuery.isFetching}
                onClick={() => void visitQuery.refetch()}
              >
                Try Again
              </button>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {/* The VISIT's status: least-advanced car wins, because the
                visit isn't done until the last car is. */}
            <StatusBadge status={isVisit ? combinedStatus(cars) : car.status} />
            <PriorityBadge priority={booking.priority} />
            {isVisit && (
              <Badge tone="neutral">
                <Car className="h-3 w-3" /> 1 Visit · {cars.length} Vehicles
              </Badge>
            )}
            {prepaid && (
              <Badge tone="info">
                <CreditCard className="h-3 w-3" /> Prepaid
              </Badge>
            )}
            {booking.source === "whatsapp" && <Badge tone="success">Booked Via WhatsApp</Badge>}
            {booking.source === "staff" && <Badge tone="neutral">Booked By Staff</Badge>}
            {car.completed_by_role === "manager" && <Badge tone="success">Done By Manager</Badge>}
            {isStaff && editedCars.map((c) => <CustomerEditedChip key={c.id} booking={c} withTime />)}
            {noGps && (
              <Badge tone="warning">
                <WifiOff className="h-3 w-3" /> Reached Without GPS
              </Badge>
            )}
            {flagged.map((f) => (
              <Badge key={f.id} tone="error">
                <AlertTriangle className="h-3 w-3" /> {issueLabel(f.issue_flag)}
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

          <Section title={isVisit ? "Customer & Vehicles" : "Customer & Vehicle"} icon={UserIcon}>
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
                        <span className="text-[var(--color-text-secondary)]">{i + 1}.</span> {carLine(c) || c.vehicle_registration_number}
                      </span>
                      <span className="block text-xs text-[var(--color-text-secondary)]">
                        {toTitle(bookingServiceLabel(c))}
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
                value={carLine(booking)}
              />
            )}
            {/* The 4-digit arrival code (quick-booking model) — so staff can
                read it out to a customer who lost their confirmation. */}
            {booking.service_code && <Row label="Service Code" value={<span className="font-mono-num tracking-[0.2em]">{booking.service_code}</span>} />}
          </Section>

          <Section title="Service & Location" icon={Wrench}>
            {!isVisit && <Row label="Service" value={toTitle(bookingServiceLabel(booking, "—"))} />}
            <Row label="Service Center" value={centerName} />
            <Row
              label="Slot"
              value={
                <span className="font-mono-num">
                  {new Date(booking.scheduled_date).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })} · {formatSlot(booking.scheduled_slot)}
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
                        Open In Maps
                      </a>
                    )}
                  </span>
                }
              />
            )}
            {travel?.store_to_customer && (
              <Row
                label="From Center"
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
            <Row label="Captain" value={car.completed_by_role === "manager" ? "Done By The Manager" : captainName || (car.captain_id ? "Assigned" : "Not Yet Assigned")} />
          </Section>

          <Section title="Payment" icon={CreditCard}>
            {/* One visit is one bill. The per-car lines are still shown,
                because a captain collecting cash needs the total, and the
                office reconciling it needs the split. */}
            {isVisit &&
              cars.map((c) => (
                <Row
                  key={c.id}
                  label={carLine(c) || c.booking_number}
                  value={<span className="font-mono-num">₹{c.total_amount}</span>}
                />
              ))}
            {travelCharge > 0 && (
              <Row
                label="Distance Charge"
                value={
                  <span className="font-mono-num">
                    ₹{travelCharge}
                    {travelKm != null && <span className="ml-1 text-xs font-normal text-[var(--color-text-secondary)]">· {travelKm} km, in the total</span>}
                  </span>
                }
              />
            )}
            <Row
              label={isVisit ? `Total · ${cars.length} Vehicles` : "Amount"}
              value={<span className="font-mono-num">₹{visitTotal}</span>}
            />
            {cancellationCharge > 0 && (
              <Row
                label="Previous Cancellation Charge"
                value={
                  <span className="font-mono-num">
                    ₹{cancellationCharge}
                    <span className="ml-1 text-xs font-normal text-[var(--color-text-secondary)]">· in the total</span>
                  </span>
                }
              />
            )}
            {isStaff && managerDiscount > 0 && (
              <Row
                label="Manager Discount"
                value={
                  <span className="font-mono-num">
                    −₹{managerDiscount}
                    {(discountCar?.manager_discount_by_name || discountCar?.manager_discount_at) && (
                      <span className="block text-xs font-normal text-[var(--color-text-secondary)]">
                        {[discountCar?.manager_discount_by_name ? `By ${discountCar.manager_discount_by_name}` : "", discountCar?.manager_discount_at ? formatDateTime(asUtcInstant(discountCar.manager_discount_at)) : ""]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    )}
                  </span>
                }
              />
            )}
            {money.carried > 0 && (
              <Row
                label="Previous Balance Due"
                value={
                  <span className="font-mono-num">
                    ₹{Math.round(money.carried)}
                    <span className="ml-1 text-xs font-normal text-[var(--color-text-secondary)]">· in the total</span>
                  </span>
                }
              />
            )}
            {money.wallet > 0 && <Row label="Paid From Wallet" value={<span className="font-mono-num">−₹{Math.round(money.wallet)}</span>} />}
            <Row label="Method" value={toTitle(booking.payment_method)} />
            {isStaff && !refundCars.length && (money.paid > 0 || money.due > 0) && (
              <>
                <Row label="Paid" value={<span className="font-mono-num text-[var(--color-success)]">₹{Math.round(money.paid)}</span>} />
                <Row
                  label="Due"
                  value={<span className={`font-mono-num ${money.due > 0 ? "font-bold text-amber-700" : ""}`}>₹{Math.round(money.due)}</span>}
                />
              </>
            )}
            <Row
              label="Status"
              value={
                refundCars.length ? (
                  <Badge tone={refundDone ? "neutral" : "warning"}>{refundDone ? `Refunded ₹${Math.round(refundAmount)}` : `Refund Due ₹${Math.round(refundAmount)}`}</Badge>
                ) : (
                  <Badge tone={paymentPending ? paymentTone(visitPayStatus) : "success"}>{paymentPending ? PAYMENT_STATUS_LABELS[visitPayStatus] : "Paid"}</Badge>
                )
              }
            />
            {isStaff && <AddedOnSiteLines cars={cars} showCar={isVisit} />}
            {refundDone && refundedAt && <Row label="Refunded On" value={<span className="font-mono-num text-xs">{formatDateTime(asUtcInstant(refundedAt))}</span>} />}
            {showTip && (
              <Row
                label="Tip (Included In Amount)"
                value={
                  <span className="flex items-center gap-2">
                    <span className="font-mono-num">
                      <span className="whitespace-nowrap">{visitTip > 0 ? `₹${visitTip} (${TIP_METHOD_LABELS[tipMethod]})` : "—"}</span>
                      {visitTip > 0 && tipSaved == null && (tipCar?.tip_updated_by_name || tipCar?.tip_updated_at) && (
                        <span className="block text-xs font-normal text-[var(--color-text-secondary)]">
                          {[tipCar?.tip_updated_by_name ? `By ${tipCar.tip_updated_by_name}` : "", tipCar?.tip_updated_at ? formatDateTime(asUtcInstant(tipCar.tip_updated_at)) : ""].filter(Boolean).join(" · ")}
                        </span>
                      )}
                    </span>
                    {canEditTip && (
                      <button type="button" onClick={() => setTipOpen(true)} className="text-xs font-semibold text-[var(--color-primary)] hover:underline">
                        {visitTip > 0 ? "Edit" : "Add Tip"}
                      </button>
                    )}
                  </span>
                }
              />
            )}
            {isStaff && paybacks.length > 0 && (
              <div className="mt-1 space-y-1 border-t border-gray-100 pt-2" data-testid="drawer-paybacks">
                {paybacks.map((p, i) => (
                  <p key={p.id || i} className="text-sm text-[var(--color-text-primary)]">
                    <span className="font-semibold">
                      Paid Back <span className="font-mono-num">₹{Math.round(p.amount)}</span>
                      {p.by_name ? ` By ${p.by_name}` : ""}
                    </span>
                    <span className="text-[var(--color-text-secondary)]">
                      {[
                        p.reason_label || (p.reason ? PAYBACK_REASON_LABELS[p.reason as PaybackReason] || toTitle(p.reason) : ""),
                        p.method ? PAYOUT_METHOD_LABELS[p.method as PayoutMethod] || toTitle(p.method) : "",
                        p.reference ? `Ref ${p.reference}` : "",
                        p.carNumber || "",
                      ]
                        .filter(Boolean)
                        .map((x) => ` · ${x}`)
                        .join("")}
                    </span>
                    {p.at && <span className="block text-xs text-[var(--color-text-secondary)]">{formatDateTime(asUtcInstant(p.at))}</span>}
                  </p>
                ))}
                {paidBackTotal > 0 && <Row label="Paid Back Total" value={<span className="font-mono-num">₹{Math.round(paidBackTotal)}</span>} />}
              </div>
            )}
          </Section>

          {/* Below this line everything is about ONE car: its own plate
              check, its own photos, its own clock. A visit has several, so
              the manager picks which one to look at rather than being shown
              a merged timeline that describes no actual car. */}
          {isVisit && (
            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-text-secondary)]">
                <Car className="h-3.5 w-3.5" /> Work On Each Vehicle
              </p>
              <div className="flex flex-wrap gap-2">
                {cars.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setCarId(c.id)}
                    className={`min-h-11 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors sm:min-h-0 ${
                      c.id === car.id
                        ? "border-black bg-[var(--color-primary-light)] text-[var(--color-text-primary)]"
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
            <TimelineRow label="Booking Created" value={car.created_at} />
            <TimelineRow label="Manager Notified" value={car.manager_notified_at} />
            <TimelineRow label="Captain Assigned" value={car.assigned_at} />
            <TimelineRow label="Captain Heading Out" value={car.heading_at} />
            <TimelineRow label="Vehicle Verified (Arrived)" value={car.vehicle_verified_at} />
            <TimelineRow label="Service Started" value={car.service_started_at} />
            <TimelineRow label="Service Completed" value={car.completed_at} />
            <TimelineRow label="Closed" value={car.closed_at} />
            {car.delay_minutes != null && car.delay_minutes > 0 && (
              <p className="mt-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--color-error)]">
                <AlertTriangle className="h-3.5 w-3.5" /> {car.delay_minutes} min over expected duration
              </p>
            )}
            {isStaff && <HistoryChanges rows={car.status_history} />}
          </Section>

          {/* Every workflow tap's captured GPS, each one openable in
              Google Maps — when a geofence flag says "captain was 900m
              from the customer", THIS is where the manager sees exactly
              where that tap happened, so there's evidence to put in
              front of the captain instead of just a distance number. */}
          {(car.heading_location || car.arrival_location || car.before_photo || car.after_photo) && (
            <Section title={isVisit ? `Location Checks · ${carTag(car)}` : "Location Checks"} icon={MapPin}>
              <LocationCheckRow label="Started Heading From" point={car.heading_location} at={car.heading_at} />
              <LocationCheckRow
                label={'"I\'ve Reached" Tapped At'}
                point={car.arrival_location}
                at={car.vehicle_verified_at}
                flagged={car.arrival_flagged}
                distanceM={car.arrival_distance_m}
              />
              <LocationCheckRow
                label="Before-Photo Taken At"
                point={car.before_photo}
                at={car.before_photo?.captured_at}
                flagged={car.before_photo_flagged}
                distanceM={car.before_photo_distance_m}
              />
              <LocationCheckRow
                label="After-Photo Taken At"
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
            {reviewQuery.isError && review === undefined ? (
              <ErrorState message="Couldn't load the review." onRetry={() => void reviewQuery.refetch()} busy={reviewQuery.isFetching} className="p-4" />
            ) : review ? (
              <div className="space-y-2">
                {(review.captain_rating != null || review.rating != null) && <StarRow label="Captain" rating={review.captain_rating ?? review.rating ?? 0} />}
                <StarRow label="Service" rating={review.service_rating ?? review.rating ?? 0} />
                {(review.captain_comment || review.service_comment || review.comment) && (
                  <p className="text-sm text-[var(--color-text-primary)]">{review.captain_comment || review.service_comment || review.comment}</p>
                )}
              </div>
            ) : (
              <p className="text-sm text-[var(--color-text-secondary)]">No Review Yet</p>
            )}
          </Section>
        </div>
      )}
      <AddServiceDialog booking={addFor} onClose={() => setAddFor(null)} onAdded={() => void (groupId ? visitQuery.refetch() : freshQuery.refetch())} />
      <TipModal
        booking={tipOpen ? doneCar : null}
        currentTip={visitTip}
        currentMethod={tipMethod}
        onClose={() => setTipOpen(false)}
        onSaved={(tip, method) => setTipSaved({ tip, method })}
      />
      {isStaff && booking?.customer_id && (
        <PaybackDialog
          open={paybackOpen}
          onClose={() => setPaybackOpen(false)}
          customerId={booking.customer_id}
          customerName={booking.customer_name}
          bookings={paybackChoices}
          defaultBookingId={paybackChoices.some((o) => o.id === car?.id) ? car?.id : paybackChoices[0]?.id}
        />
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
        <MapPin className="h-3 w-3" /> Open In Maps
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
        <Flag className="h-3 w-3" /> High Priority
      </Badge>
    );
  }
  if (priority === "low") return <Badge tone="neutral">Low Priority</Badge>;
  return <Badge tone="neutral">Medium Priority</Badge>;
}
