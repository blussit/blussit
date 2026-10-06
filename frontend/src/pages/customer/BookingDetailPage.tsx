import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { BadgeCheck, CheckCircle2, ChevronLeft, Clock, LifeBuoy, Navigation, Pencil, Phone, RotateCcw, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { paymentApi } from "../../api/payment";
import { complaintApi, reviewApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { Button, Card, CardBody, CardHeader, Input, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { useLiveChannel } from "../../lib/socket";
import { format, formatDateTime, formatSlot } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { PaymentCancelled, PaymentFailed, PaymentNeedsAttention, PaymentPendingConfirmation, paymentErrorMessage, payWithRazorpay, sentence } from "../../lib/razorpay";
import { vehicleLabel } from "../../lib/constants";
import { bookingEventId, trackPurchase } from "../../lib/metaPixel";
import type { Booking } from "../../types";

const PAYMENT_METHOD_LABELS: Record<string, string> = { cash: "Cash on service", online: "Online", subscription: "Plan" };

/** Nothing more will happen to it: cancelled, or done and settled. A done
 * but still-unpaid booking stays live — the captain may be collecting. */
function isFinished(b: Booking | undefined): boolean {
  if (!b) return false;
  if (b.status === "cancelled") return true;
  return b.status === "completed" && (b.payment_status === "paid" || b.payment_status === "refunded" || !(b.total_amount > 0));
}

export default function BookingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const confirm = useConfirm();
  const isCustomer = user?.role === "customer";
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [newDate, setNewDate] = useState("");
  const [newSlot, setNewSlot] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [captainRating, setCaptainRating] = useState(5);
  const [captainComment, setCaptainComment] = useState("");
  const [serviceRating, setServiceRating] = useState(5);
  const [serviceComment, setServiceComment] = useState("");
  const [error, setError] = useState("");
  // "Need help?" opens the request form right here — it used to navigate
  // to the standalone Support page, which meant the "x" close button left
  // the customer stranded there instead of back on their booking.
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportSubject, setSupportSubject] = useState("");
  const [supportDescription, setSupportDescription] = useState("");
  const [supportError, setSupportError] = useState("");
  // What the last pay attempt on THIS screen came to — failed (with the
  // bank's reason), still being confirmed, or parked for review.
  const [payNote, setPayNote] = useState<{ tone: "info" | "error"; text: string } | null>(null);

  const bookingQueryKey = ["booking", id];
  const { data: booking, isLoading } = useQuery({
    queryKey: bookingQueryKey,
    queryFn: () => bookingApi.get(id as string),
    enabled: !!id,
    // Live-pushed over "booking:{id}" below (status/assignment/priority
    // changes) — this interval is the fallback for while the socket is
    // reconnecting, not the primary way this page stays current. Nothing
    // left to watch once the visit is finished.
    refetchInterval: (query) => (isFinished(query.state.data) ? false : 45000),
  });

  // A car booked as part of a multi-vehicle visit is only half the story on
  // its own — the customer booked ONE thing and expects to see all of it.
  const groupId = booking?.booking_group_id || null;
  const { data: visitBookings } = useQuery({
    queryKey: ["booking-group", groupId],
    queryFn: () => bookingApi.getGroup(groupId as string),
    enabled: !!groupId,
    refetchInterval: (query) => (query.state.data?.every(isFinished) ? false : 45000),
  });
  const visit = groupId && visitBookings?.length ? visitBookings : null;
  const live = !!booking && !(visit ? visit.every(isFinished) : isFinished(booking));
  /** The whole visit's outstanding amount — what the customer actually owes. */
  const visitTotal = (visit || (booking ? [booking] : [])).reduce((sum, b) => sum + (b.total_amount || 0), 0);
  // Status timeline + before/after photos: every car on the visit, not
  // just whichever one this page happens to be for.
  const trailCars = visit || (booking ? [booking] : []);
  const multiCar = trailCars.length > 1;
  // Charged once per visit (on its first car) and already inside total_amount.
  const travelCharge = trailCars.reduce((sum, b) => sum + (b.travel_charge || 0), 0);
  const travelKm = trailCars.find((b) => (b.travel_charge || 0) > 0)?.travel_charge_km;
  const prepaidOnly = trailCars.some((b) => b.prepaid_only);
  // Founder rule: a prepaid service can never fall back to cash.
  const cashAllowed = !prepaidOnly && booking?.status === "awaiting_payment";
  const carLabel = (car: (typeof trailCars)[number]) =>
    vehicleLabel(car);

  useLiveChannel(id && live ? `booking:${id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: bookingQueryKey });
  });

  // A failed attempt (retryable) or money received that couldn't be
  // applied (being fixed/refunded) — facts the booking itself doesn't carry.
  const paymentStateKey = ["booking-payment-state", id];
  const { data: paymentState } = useQuery({
    queryKey: paymentStateKey,
    queryFn: () => paymentApi.bookingState(id as string),
    enabled: !!id && isCustomer,
    refetchInterval: live ? 45000 : false,
  });

  // Just this booking's review — the unfiltered list stops at the newest 100.
  const { data: myReviews } = useQuery({ queryKey: ["my-reviews", "for", id], queryFn: () => reviewApi.mine(id ? [id] : undefined), enabled: isCustomer && !!id });
  const myReview = myReviews?.find((r) => r.booking_id === id);

  // A completed-but-unrated booking opens the rating window by itself —
  // the moment the customer lands here (from the list's "Rate" stars, a
  // notification, or the wash finishing while they watch), the ask is
  // right in front of them instead of buried behind a button. Once per
  // visit: closing it doesn't re-trigger until the next page open, and it
  // only fires after the reviews query resolves (myReviews !== undefined)
  // so an already-rated booking never flashes the modal.
  const autoOpenedRef = useRef(false);
  useEffect(() => {
    const requestedReview = searchParams.get("review") === "1";
    if (autoOpenedRef.current || !isCustomer) return;
    if (requestedReview && booking && myReviews !== undefined) {
      autoOpenedRef.current = true;
      openReview();
      return;
    }
    if (booking?.status !== "completed" || myReviews === undefined || myReview) return;
    autoOpenedRef.current = true;
    openReview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booking?.status, myReviews, searchParams]);

  const openReview = () => {
    setError("");
    if (myReview) {
      setCaptainRating(myReview.captain_rating ?? 5);
      setCaptainComment(myReview.captain_comment || "");
      setServiceRating(myReview.service_rating);
      setServiceComment(myReview.service_comment || "");
    } else {
      setCaptainRating(5);
      setCaptainComment("");
      setServiceRating(5);
      setServiceComment("");
    }
    setReviewOpen(true);
  };

  const cancelMutation = useMutation({
    // Returns different shapes for a visit vs a single booking; the caller
    // only cares that it succeeded, so normalise to void.
    mutationFn: async (): Promise<void> => {
      if (groupId) await bookingApi.cancelGroup(groupId, cancelReason);
      else await bookingApi.cancel(id as string, cancelReason);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      setCancelOpen(false);
      setCancelReason("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const rescheduleMutation = useMutation({
    mutationFn: () => bookingApi.reschedule(id as string, newDate, newSlot),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      setRescheduleOpen(false);
      setNewDate("");
      setNewSlot("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const reviewMutation = useMutation({
    mutationFn: () => {
      // A job the manager did himself has no captain to rate.
      const captainPart = booking?.captain_id ? { captain_rating: captainRating, captain_comment: captainComment } : {};
      return myReview
        ? reviewApi.update(myReview.id, { ...captainPart, service_rating: serviceRating, service_comment: serviceComment })
        : reviewApi.create({ booking_id: id as string, ...captainPart, service_rating: serviceRating, service_comment: serviceComment });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      queryClient.invalidateQueries({ queryKey: ["my-reviews"] });
      setReviewOpen(false);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  // Paying (or switching to cash) only CONFIRMS a booking that was waiting
  // on payment — that's the Meta Purchase. Paying a confirmed cash booking
  // online later isn't a new sale. Read when the action starts, before a
  // live update can change the status under it.
  const confirmsBooking = useRef(false);
  const trackConfirmed = () => {
    if (!confirmsBooking.current || !booking || !(visitTotal > 0)) return;
    trackPurchase({
      value: visitTotal,
      eventId: bookingEventId(groupId, booking.id),
      contentType: "booking",
      numItems: trailCars.length,
    });
  };

  const payMutation = useMutation({
    mutationFn: () => {
      setPayNote(null);
      setError("");
      confirmsBooking.current = booking?.status === "awaiting_payment";
      return payWithRazorpay(
        // A visit is paid for once — every car on it, one order.
        groupId ? { purpose: "booking_group", booking_group_id: groupId } : { purpose: "booking", booking_id: id as string },
        { name: user?.full_name, email: user?.email, contact: user?.phone },
        undefined,
        {
          onConfirming: () => setPayNote({ tone: "info", text: "Payment done — confirming it with the bank…" }),
          onFailed: (reason) => setPayNote({ tone: "error", text: `Payment failed — ${reason}` }),
        }
      );
    },
    onSuccess: () => {
      setPayNote(null);
      trackConfirmed();
    },
    onError: (err) => {
      if (err instanceof PaymentFailed) {
        setPayNote({ tone: "error", text: `Payment failed — ${err.reason} Try again${cashAllowed ? ", or pay cash instead" : ""}.` });
      } else if (err instanceof PaymentPendingConfirmation || err instanceof PaymentNeedsAttention) {
        setPayNote({ tone: "info", text: err.message });
      } else if (err instanceof PaymentCancelled) {
        setPayNote(null); // closed the modal without paying — nothing to report
      } else {
        setError(paymentErrorMessage(err));
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: bookingQueryKey });
      queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      queryClient.invalidateQueries({ queryKey: paymentStateKey });
    },
  });

  const switchToCashMutation = useMutation({
    // One decision for the whole visit — switching one car would leave the others unpaid.
    mutationFn: async (): Promise<void> => {
      confirmsBooking.current = booking?.status === "awaiting_payment";
      if (groupId) await bookingApi.switchGroupToCash(groupId);
      else await bookingApi.switchToCash(id as string);
    },
    onSuccess: () => {
      trackConfirmed();
      queryClient.invalidateQueries({ queryKey: bookingQueryKey });
      queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const deleteReviewMutation = useMutation({
    mutationFn: () => reviewApi.remove(myReview!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      queryClient.invalidateQueries({ queryKey: ["my-reviews"] });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const supportSubjectValid = supportSubject.trim().length >= 3;
  const supportDescriptionValid = supportDescription.trim().length >= 5;
  const supportMutation = useMutation({
    mutationFn: () => complaintApi.create({ booking_id: id as string, subject: supportSubject.trim(), description: supportDescription.trim() }),
    onSuccess: () => {
      setSupportOpen(false);
      setSupportSubject("");
      setSupportDescription("");
      setSupportError("");
    },
    onError: (err) => setSupportError(getErrorMessage(err)),
  });

  if (isLoading || !booking) return <PageLoader />;

  // Cancellation policy phase 1 (mirrors the backend's enforcement):
  // self-cancel only while the booking is still unassigned AND more than
  // 4 hours before the slot. After that the button disappears — the hint
  // below points at support instead.
  const slotStartMs = (() => {
    const d = new Date(booking.scheduled_date);
    const [h, m] = (booking.scheduled_slot || "0:0").split("-")[0].split(":").map(Number);
    d.setHours(h || 0, m || 0, 0, 0);
    return d.getTime();
  })();
  const insideCancelLock = Date.now() > slotStartMs - 4 * 60 * 60 * 1000;
  const unassigned = ["pending", "rescheduled"].includes(booking.status);
  // Not a real booking yet: the customer chose to pay online and hasn't
  // finished. Nothing was dispatched and no money was taken, so the
  // 4-hour lock (which protects a dispatched job) doesn't apply — matching
  // the backend, which lets them walk away from it at any time.
  const awaitingPayment = booking.status === "awaiting_payment";
  // Only while nothing newer (this screen's own attempt) says otherwise.
  const failedReason = !payNote && !payMutation.isPending ? paymentState?.last_failure?.reason : undefined;
  const lastFailure = failedReason ? sentence(failedReason) : undefined;
  const canCancel = isCustomer && (awaitingPayment || (unassigned && !insideCancelLock));
  const cancelLockHint =
    isCustomer && !["completed", "cancelled"].includes(booking.status) && !canCancel
      ? !unassigned
        ? "A captain is assigned, so it can't be cancelled online."
        : "Cancellations close 4 hours before your slot. Message us on WhatsApp for help."
      : null;
  // Mirrors the backend's reschedule guard — once a captain is on the way
  // or mid-service, rescheduling would pull the booking out from under
  // real, unfinished work with no notice; cancel or wait it out instead.
  const canReschedule =
    isCustomer &&
    !["completed", "cancelled", "captain_on_the_way", "service_started", "awaiting_payment"].includes(booking.status);
  const cancelReasonValid = cancelReason.trim().length >= 3;
  // Same wording as the booking lists: every service on the visit, once each.
  const title = [...new Set(trailCars.map((b) => b.combo_name || b.service_names?.join(", ") || "Service"))].join(" + ");

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div className="flex items-start gap-3">
        <button onClick={() => navigate(-1)} aria-label="Back" className="-ml-2 rounded-full p-2 hover:bg-gray-100">
          <ChevronLeft className="h-5 w-5" />
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold text-black">{title}</h1>
          <p className="mt-0.5 text-sm text-gray-500">
            {format(booking.scheduled_date)} · {formatSlot(booking.scheduled_slot)}
            {visit ? ` · ${visit.length} vehicles` : ""}
          </p>
          <p className="font-mono-num text-xs text-gray-400">{booking.booking_number}</p>
        </div>
        <StatusBadge status={booking.status} />
      </div>

      {/* Money that arrived but couldn't be applied — said plainly, so nobody pays twice. */}
      {isCustomer && paymentState?.attention && (
        <Card className="p-4 sm:p-5">
          <p className="font-semibold text-black">Payment received — under review</p>
          <p className="mt-1 text-sm text-gray-600">
            ₹{paymentState.attention.amount} · {paymentState.attention.message}
          </p>
        </Card>
      )}

      {/* Not booked until paid: finish paying, or (unless prepaid only) have the captain collect cash. */}
      {isCustomer && awaitingPayment && (
        <Card className="p-4 sm:p-5">
          <p className="font-semibold text-black">
            {paymentState?.confirming ? "Confirming your payment…" : lastFailure ? "Payment failed" : "Payment pending"}
          </p>
          <p className="mt-1 text-sm text-gray-600">
            {lastFailure
              ? `${lastFailure} Try again${cashAllowed ? ", or pay cash instead" : ""} — unpaid slots are released shortly.`
              : `Pay to confirm your slot${visit ? ` — one payment covers all ${visit.length} vehicles` : ""}. Unpaid slots are released shortly.`}
          </p>
          {payNote && (
            <p className={`mt-2 text-sm ${payNote.tone === "error" ? "text-[var(--color-error)]" : "text-gray-700"}`}>{payNote.text}</p>
          )}
          <div className="mt-4 flex flex-wrap gap-2.5">
            <Button variant="info" isLoading={payMutation.isPending} onClick={() => payMutation.mutate()}>
              {lastFailure ? `Try again — ₹${visitTotal}` : `Pay ₹${visitTotal} now`}
            </Button>
            {!prepaidOnly && (
              <Button variant="outline" isLoading={switchToCashMutation.isPending} onClick={() => switchToCashMutation.mutate()}>
                Pay cash instead
              </Button>
            )}
            {/* Nothing is booked yet, so the wizard reopens with every choice filled in. */}
            <Button variant="ghost" onClick={() => navigate(`/app/book?edit=${booking.id}`)}>
              <Pencil className="h-4 w-4" /> Edit booking
            </Button>
          </div>
        </Card>
      )}

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      {payNote && !awaitingPayment && (
        <p className={`text-sm ${payNote.tone === "error" ? "text-[var(--color-error)]" : "text-gray-700"}`}>{payNote.text}</p>
      )}

      <div className="flex flex-wrap gap-2.5">
        {/* Founder rule: any unpaid booking can be paid online at any time, even one booked as cash. */}
        {isCustomer &&
          booking.payment_status === "pending" &&
          booking.status !== "cancelled" &&
          !awaitingPayment &&
          booking.total_amount > 0 && (
            <Button variant="info" isLoading={payMutation.isPending} onClick={() => payMutation.mutate()}>
              Pay ₹{visitTotal} online
            </Button>
          )}
        {/* The wizard replays vehicle, services and address; only the date and slot are new. */}
        {isCustomer && ["completed", "cancelled"].includes(booking.status) && (
          <Button variant="info" onClick={() => navigate(`/app/book?repeat=${booking.id}`)}>
            <RotateCcw className="h-4 w-4" /> Book again
          </Button>
        )}
        {isCustomer && booking.status === "completed" && !myReview && (
          <Button variant="outline" onClick={openReview}>
            <Star className="h-4 w-4" /> Rate this service
          </Button>
        )}
        {canReschedule && (
          <Button variant="outline" onClick={() => setRescheduleOpen(true)}>
            Reschedule
          </Button>
        )}
        {canCancel && (
          <Button variant="outline" onClick={() => setCancelOpen(true)}>
            Cancel booking
          </Button>
        )}
        {isCustomer && (
          <Button
            variant="ghost"
            onClick={() => {
              setSupportError("");
              setSupportOpen(true);
            }}
          >
            <LifeBuoy className="h-4 w-4" /> Need help?
          </Button>
        )}
        {cancelLockHint && <p className="w-full text-xs text-gray-500">{cancelLockHint}</p>}
      </div>

      {/* The one number the customer needs on the day — the captain asks for it on arrival. */}
      {booking.service_code && !["completed", "cancelled"].includes(booking.status) && (
        <Card className="flex items-center justify-between gap-3 px-4 py-3">
          <div>
            <p className="text-sm font-medium text-black">Service code</p>
            <p className="text-xs text-gray-500">Share it with the captain on arrival.</p>
          </div>
          <p className="font-mono-num text-2xl font-bold tracking-[0.3em] text-black">{booking.service_code}</p>
        </Card>
      )}

      {booking.captain_profile && booking.status !== "cancelled" && (
        <Card className="flex items-center gap-4 p-4">
          {booking.captain_profile.photo_url ? (
            <img src={booking.captain_profile.photo_url} alt={booking.captain_profile.full_name || "Captain"} className="h-14 w-14 shrink-0 rounded-full object-cover" />
          ) : (
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gray-100 font-display text-lg font-bold text-black">
              {(booking.captain_profile.full_name || "C").trim().charAt(0).toUpperCase()}
            </span>
          )}
          <div className="min-w-0 flex-1">
            <p className="text-xs text-gray-500">Your captain</p>
            <p className="flex flex-wrap items-center gap-1.5 font-semibold text-black">
              {booking.captain_profile.full_name || "Captain"}
              {booking.captain_profile.verified && (
                <span className="inline-flex items-center gap-0.5 rounded-full bg-green-50 px-1.5 py-0.5 text-[11px] font-medium text-green-700">
                  <BadgeCheck className="h-3 w-3" /> Verified
                </span>
              )}
            </p>
            {booking.captain_profile.employee_id && (
              <p className="font-mono-num text-xs text-gray-400">ID {booking.captain_profile.employee_id}</p>
            )}
          </div>
          {booking.captain_profile.phone && (
            <a
              href={`tel:${booking.captain_profile.phone}`}
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-gray-300 bg-white px-3.5 py-2 text-sm font-medium text-black transition-colors hover:border-gray-400 hover:bg-gray-50"
            >
              <Phone className="h-4 w-4" /> Call
            </a>
          )}
        </Card>
      )}

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-black">Price details</h2>
        </CardHeader>
        <CardBody className="space-y-3 text-sm">
          {/* A visit is one booking to the customer: every vehicle on it, one total. */}
          {visit ? (
            visit.map((car) => (
              <div key={car.id} className="flex justify-between gap-3">
                <span className="min-w-0 text-gray-600">
                  <span className="block font-medium text-black">{car.vehicle_snapshot ? vehicleLabel(car) : "Vehicle"}</span>
                  {car.combo_name || car.service_names?.join(", ") || "Service"}
                </span>
                <span className="font-mono-num">₹{(car.total_amount || 0) - (car.travel_charge || 0)}</span>
              </div>
            ))
          ) : (
            <>
              <div className="flex justify-between">
                <span className="text-gray-600">Subtotal</span>
                <span className="font-mono-num">₹{booking.subtotal}</span>
              </div>
              {booking.discount_amount > 0 && (
                <div className="flex justify-between text-[var(--color-success)]">
                  <span>Discount</span>
                  <span className="font-mono-num">-₹{booking.discount_amount}</span>
                </div>
              )}
            </>
          )}
          {travelCharge > 0 && (
            <div className="flex justify-between">
              <span className="text-gray-600">
                Distance charge{travelKm ? ` · ${Math.round(travelKm * 10) / 10} km` : ""}
              </span>
              <span className="font-mono-num">₹{travelCharge}</span>
            </div>
          )}
          <div className="flex justify-between border-t border-gray-100 pt-3 text-base font-bold">
            <span>Total</span>
            <span className="font-mono-num">₹{visitTotal}</span>
          </div>
          <div className="flex justify-between text-xs text-gray-500">
            <span>Payment</span>
            <span>
              {PAYMENT_METHOD_LABELS[booking.payment_method] || (booking.payment_method || "—").replace(/_/g, " ")}
              {booking.payment_status === "paid" ? " · Paid" : ""}
            </span>
          </div>
        </CardBody>
      </Card>

      {/* Every car on a visit has its own timeline, shown one after another. */}
      {trailCars.some((car) => !!car.status_history?.length) && (
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-black">Status timeline</h2>
          </CardHeader>
          <CardBody className="space-y-6">
            {trailCars.map(
              (car) =>
                !!car.status_history?.length && (
                  <div key={car.id}>
                    {multiCar && <p className="mb-3 text-xs font-semibold text-black">{carLabel(car)}</p>}
                    <div className="space-y-4">
                      {car.status_history!.map((h, i) => (
                        <div key={i} className="flex gap-3">
                          <div className="flex flex-col items-center">
                            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100 text-black">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                            </span>
                            {i < (car.status_history?.length || 0) - 1 && <div className="mt-1 h-full w-px flex-1 bg-gray-200" />}
                          </div>
                          <div className="pb-4">
                            <p className="text-sm font-medium capitalize text-black">{h.status.replace(/_/g, " ")}</p>
                            {h.note && <p className="text-xs text-gray-500">{h.note}</p>}
                            <p className="mt-0.5 text-xs text-gray-400">{formatDateTime(h.created_at)}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )
            )}
          </CardBody>
        </Card>
      )}

      {/* Issue flags ("Captain started late", geofence alerts…) are
          internal manager↔captain ops — the API redacts them for customer
          responses (_CUSTOMER_HIDDEN_FIELDS) and no banner renders here. */}

      {/* Internal to manager/captain — the specific lateness stage and pay
          penalty are operational/financial detail, not something the
          customer needs. */}
      {!isCustomer && booking.captain_start_stage && booking.captain_start_stage !== "on_time" && booking.captain_start_stage !== "early" && (
        <Card className="border border-[var(--color-warning)]/40">
          <CardBody className="flex items-start gap-3">
            <Clock className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-warning)]" />
            <div>
              <p className="font-semibold text-[var(--color-text-primary)]">
                Captain started {booking.captain_start_stage === "severely_late" ? "significantly late" : "late"}
              </p>
              {booking.late_penalty_pct ? (
                <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                  A {booking.late_penalty_pct}% penalty was applied to the captain's service fee for this booking.
                </p>
              ) : null}
            </div>
          </CardBody>
        </Card>
      )}

      {trailCars.some((car) => car.heading_at || car.before_photo || car.after_photo) && (
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-black">Service updates</h2>
          </CardHeader>
          <CardBody className="space-y-6">
            {trailCars.map(
              (car) =>
                (car.heading_at || car.before_photo || car.after_photo) && (
                  <div key={car.id} className={multiCar ? "space-y-4 border-b border-gray-100 pb-6 last:border-b-0 last:pb-0" : "space-y-5"}>
                    {multiCar && <p className="text-xs font-semibold text-black">{carLabel(car)}</p>}
                    {car.heading_at && (
                      <div className="flex items-start gap-3 text-sm">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-black">
                          <Navigation className="h-4 w-4" />
                        </span>
                        <div>
                          <p className="font-medium text-black">Captain started heading over</p>
                          <p className="text-xs text-gray-500">{formatDateTime(car.heading_at)}</p>
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      {car.before_photo && (
                        <div>
                          <p className="mb-2 text-xs font-medium text-gray-500">Before</p>
                          <img src={car.before_photo.image_url} alt="Before service" className="h-40 w-full rounded-xl object-cover" />
                          <p className="mt-1.5 text-xs text-gray-500">{formatDateTime(car.before_photo.captured_at)}</p>
                        </div>
                      )}
                      {car.after_photo && (
                        <div>
                          <p className="mb-2 text-xs font-medium text-gray-500">After</p>
                          <img src={car.after_photo.image_url} alt="After service" className="h-40 w-full rounded-xl object-cover" />
                          <p className="mt-1.5 text-xs text-gray-500">{formatDateTime(car.after_photo.captured_at)}</p>
                        </div>
                      )}
                    </div>
                  </div>
                )
            )}

            {/* Captain payout / platform margin — internal financial detail,
                only ever shown to manager/admin. */}
            {booking.captain_earning != null && (user?.role === "manager" || user?.role === "admin") && (
              <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-4 text-sm">
                <div className="flex justify-between">
                  <span className="text-[var(--color-text-secondary)]">Captain's fee</span>
                  <span className="font-mono-num font-semibold">₹{booking.captain_earning}</span>
                </div>
                {booking.platform_earning != null && (
                  <div className="mt-1.5 flex justify-between">
                    <span className="text-[var(--color-text-secondary)]">Platform's share</span>
                    <span className="font-mono-num font-semibold">₹{booking.platform_earning}</span>
                  </div>
                )}
                <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                  {booking.wallet_settled ? "Wallet settled for this booking." : "Wallet settlement pending."}
                </p>
              </div>
            )}
          </CardBody>
        </Card>
      )}

      {isCustomer && myReview && (
        <div className="flex flex-wrap gap-2.5">
          <Button variant="outline" onClick={openReview}>
            <Star className="h-4 w-4" /> Edit review
          </Button>
          <Button
            variant="ghost"
            isLoading={deleteReviewMutation.isPending}
            onClick={async () => {
              if (await confirm({ title: "Delete your review?", message: "It can't be restored afterwards.", tone: "danger" })) deleteReviewMutation.mutate();
            }}
          >
            Delete review
          </Button>
        </div>
      )}

      <Modal open={cancelOpen} onClose={() => setCancelOpen(false)} title="Cancel booking">
        <div className="space-y-4">
          <Input
            label="Reason for cancellation"
            value={cancelReason}
            onChange={(e) => setCancelReason(e.target.value)}
            hint={!cancelReasonValid ? "At least 3 characters, so we know why." : undefined}
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button
            variant="danger"
            className="w-full"
            disabled={!cancelReasonValid}
            isLoading={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
          >
            Confirm cancellation
          </Button>
        </div>
      </Modal>

      <Modal open={rescheduleOpen} onClose={() => setRescheduleOpen(false)} title="Reschedule">
        <div className="space-y-4">
          <p className="text-sm text-gray-500">Your captain may change for the new time.</p>
          <SlotPicker serviceCenterId={booking.service_center_id} date={newDate} onDateChange={setNewDate} value={newSlot} onChange={setNewSlot} />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button
            className="w-full"
            disabled={!newDate || !newSlot}
            isLoading={rescheduleMutation.isPending}
            onClick={() => rescheduleMutation.mutate()}
          >
            Confirm reschedule
          </Button>
        </div>
      </Modal>

      <Modal open={reviewOpen} onClose={() => setReviewOpen(false)} title={myReview ? "Edit your review" : "Rate your experience"}>
        <div className="space-y-4">
          {booking.captain_id && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Captain</p>
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map((r) => (
                  <button key={r} onClick={() => setCaptainRating(r)}>
                    <Star className={`h-7 w-7 ${r <= captainRating ? "fill-amber-400 text-amber-400" : "text-gray-200"}`} />
                  </button>
                ))}
              </div>
              <Input className="mt-2" placeholder="Comment on your captain (optional)" value={captainComment} onChange={(e) => setCaptainComment(e.target.value)} />
            </div>
          )}
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Service quality</p>
            <div className="flex gap-1">
              {[1, 2, 3, 4, 5].map((r) => (
                <button key={r} onClick={() => setServiceRating(r)}>
                  <Star className={`h-7 w-7 ${r <= serviceRating ? "fill-amber-400 text-amber-400" : "text-gray-200"}`} />
                </button>
              ))}
            </div>
            <Input className="mt-2" placeholder="Comment on the service (optional)" value={serviceComment} onChange={(e) => setServiceComment(e.target.value)} />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button className="w-full" isLoading={reviewMutation.isPending} onClick={() => reviewMutation.mutate()}>
            {myReview ? "Save changes" : "Submit review"}
          </Button>
        </div>
      </Modal>

      <Modal open={supportOpen} onClose={() => setSupportOpen(false)} title="Raise a support request">
        <div className="space-y-4">
          <p className="text-xs text-gray-500">
            Booking <span className="font-mono-num">{booking.booking_number}</span>
          </p>
          <Input
            label="What went wrong?"
            value={supportSubject}
            onChange={(e) => setSupportSubject(e.target.value)}
            hint={!supportSubjectValid ? "At least 3 characters." : undefined}
          />
          <Input
            label="Tell us a bit more"
            value={supportDescription}
            onChange={(e) => setSupportDescription(e.target.value)}
            hint={!supportDescriptionValid ? "At least 5 characters." : undefined}
          />
          {supportError && <p className="text-sm text-[var(--color-error)]">{supportError}</p>}
          <Button
            className="w-full"
            disabled={!supportSubjectValid || !supportDescriptionValid}
            isLoading={supportMutation.isPending}
            onClick={() => supportMutation.mutate()}
          >
            Submit request
          </Button>
        </div>
      </Modal>
    </div>
  );
}
