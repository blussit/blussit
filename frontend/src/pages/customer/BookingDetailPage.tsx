/**
 * One booking (or a multi-car visit), as the customer sees it: a status
 * headline, the captain's card with Call / WhatsApp, the 4-digit service
 * code, a Booked → Captain assigned → On the way → Started → Completed
 * timeline (12-hour IST times), and every action that still applies — pay
 * what's due, edit (until 1 hour before), cancel (until the captain heads
 * out, with the wallet effect shown first), book again, rate, get help.
 *
 * Founder rule: NO live location or map for the customer. The page follows
 * the booking's STATUS only — a "changed" ping on the booking:{id} socket
 * plus a slow fallback poll while the visit is live; nothing here asks
 * where the captain is.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { BadgeCheck, Ban, CalendarClock, CarFront, CalendarX2, Check, CheckCircle2, CreditCard, LifeBuoy, MapPin, Navigation2, Pencil, Phone, RotateCcw, Sparkles, Star, UserCheck, type LucideIcon } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { bookingPolicyApi, vehicleTypeApi } from "../../api/catalog";
import type { BookingEditResult, CancelOutcome, CancelPreview } from "../../api/bookingEdit";
import { EditBookingSheet, EditResultSheet } from "../../components/customer/EditBookingSheet";
import { openBlussitWhatsApp } from "../../components/public/WhatsAppFloatingButton";
import { visitCarDetail, visitTypeServiceLabel } from "../../components/customer/cars";
import { addedByLine, paymentStatusLabel, visitDue, visitMoney, type BookingMoney } from "../../components/customer/money";
import { toTitle } from "../../lib/titleCase";
import { paymentApi } from "../../api/payment";
import { complaintApi, reviewApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { Input, Modal, PageLoader } from "../../components/ui";
import { btn, card, PageHeader, StatusChip, WhatsAppGlyph, whatsAppLink } from "../../components/customer/ui";
import { useLiveChannel } from "../../lib/socket";
import { bookingEventId, trackPurchase } from "../../lib/metaPixel";
import { formatDay, formatShortDate, formatSlot, todayIST } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { combinedStatus } from "../../lib/bookingGroups";
import { PaymentCancelled, PaymentFailed, PaymentNeedsAttention, PaymentPendingConfirmation, paymentErrorMessage, payWithRazorpay, sentence } from "../../lib/razorpay";
import { vehicleLabel } from "../../lib/constants";
import { bookingEventId, trackPurchase } from "../../lib/metaPixel";
import type { Booking } from "../../types";

const PAYMENT_METHOD_LABELS: Record<string, string> = { cash: "Cash On Service", online: "Online", subscription: "Plan" };
const IST = "Asia/Kolkata";

/** Nothing more will happen to it: cancelled, or done and settled. A done
 * but still-unpaid booking stays live — the captain may be collecting. */
function isFinished(b: Booking | undefined): boolean {
  if (!b) return false;
  if (b.status === "cancelled") return true;
  return b.status === "completed" && (b.payment_status === "paid" || b.payment_status === "refunded" || !(b.total_amount > 0));
}

const istDay = (iso: string) => new Intl.DateTimeFormat("en-CA", { timeZone: IST }).format(new Date(iso));
const istTime = (iso: string) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: IST });

/** "10:30 AM" on the visit's own day (or today), else "29 Sep, 10:30 AM". */
function stamp(iso: string | null | undefined, sameDayAs: string): string {
  if (!iso) return "";
  try {
    const day = istDay(iso);
    return day === sameDayAs || day === todayIST() ? istTime(iso) : `${formatShortDate(day)}, ${istTime(iso)}`;
  } catch {
    return "";
  }
}

const LIFECYCLE = ["awaiting_payment", "pending", "rescheduled", "assigned", "captain_on_the_way", "service_started", "completed"];
const reached = (status: string, step: string) => LIFECYCLE.indexOf(status) >= LIFECYCLE.indexOf(step);

interface Step {
  key: string;
  label: string;
  at: string;
  done: boolean;
  tone?: "error";
}

/** The visit's timeline: the earliest time each step happened on any car,
 *  "Completed" once the LAST car is done. */
function buildTimeline(cars: Booking[], status: string): Step[] {
  const day = cars[0]?.scheduled_date.slice(0, 10) || "";
  const fromHistory = (b: Booking, s: string) => b.status_history?.find((h) => h.status === s)?.created_at || null;
  const first = (pick: (b: Booking) => string | null | undefined) => {
    const times = cars.map(pick).filter((t): t is string => !!t).sort();
    return times[0] || null;
  };
  const last = (pick: (b: Booking) => string | null | undefined) => {
    const times = cars.map(pick).filter((t): t is string => !!t).sort();
    return times.length === cars.length ? times[times.length - 1] : null;
  };
  const cancelled = status === "cancelled";
  const live = cars.filter((b) => b.status !== "cancelled");
  // A cancelled visit shows how far it got before it stopped.
  const furthest = Math.max(0, ...cars.flatMap((b) => (b.status_history || []).map((h) => LIFECYCLE.indexOf(h.status))));
  const progress = cancelled ? LIFECYCLE[furthest] : combinedStatus(live.length ? live : cars);

  const steps: Step[] = [
    { key: "booked", label: "Booked", at: stamp(first((b) => fromHistory(b, "pending") || b.created_at), day), done: progress !== "awaiting_payment" },
    { key: "assigned", label: "Captain Assigned", at: stamp(first((b) => b.assigned_at || fromHistory(b, "assigned")), day), done: reached(progress, "assigned") },
    { key: "on_the_way", label: "On The Way", at: stamp(first((b) => b.heading_at || fromHistory(b, "captain_on_the_way")), day), done: reached(progress, "captain_on_the_way") },
    { key: "started", label: "Service Started", at: stamp(first((b) => b.service_started_at || fromHistory(b, "service_started")), day), done: reached(progress, "service_started") },
    { key: "completed", label: "Completed", at: stamp(last((b) => b.completed_at || fromHistory(b, "completed")), day), done: progress === "completed" },
  ];

  if (cancelled) {
    const at = stamp(first((b) => fromHistory(b, "cancelled") || b.closed_at), day);
    return [...steps.filter((s) => s.done && (s.at || s.key === "booked")), { key: "cancelled", label: "Cancelled", at, done: true, tone: "error" }];
  }
  // A job the manager logged as done skips the captain steps — don't show
  // them as if they happened.
  if (progress === "completed") return steps.filter((s) => s.at || s.key === "booked" || s.key === "completed");
  return steps;
}

const HERO: Record<string, { icon: LucideIcon; cls: string }> = {
  awaiting_payment: { icon: CreditCard, cls: "bg-[#FFF4E5] text-[#B25E00]" },
  pending: { icon: CalendarClock, cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  rescheduled: { icon: CalendarClock, cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  assigned: { icon: UserCheck, cls: "bg-[#E7F6EC] text-[#1E7B3C]" },
  captain_on_the_way: { icon: Navigation2, cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  service_started: { icon: Sparkles, cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  completed: { icon: CheckCircle2, cls: "bg-[#E7F6EC] text-[#1E7B3C]" },
  cancelled: { icon: Ban, cls: "bg-[#FDECEC] text-[#C62828]" },
};

function DetailRow({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 text-sm">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-[#8A94A6]" />
      <div className="min-w-0 flex-1 text-[#0E1A33]">{children}</div>
    </div>
  );
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
  // Edit Booking (date, time, address, services, notes) — opened here or
  // by ?edit=1 from My Bookings — and what the save changed.
  const [editOpen, setEditOpen] = useState(false);
  const [editResult, setEditResult] = useState<BookingEditResult | null>(null);
  // What a cancel just did to the wallet / plan wash.
  const [cancelOutcome, setCancelOutcome] = useState<CancelOutcome | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [captainRating, setCaptainRating] = useState(5);
  const [captainComment, setCaptainComment] = useState("");
  const [serviceRating, setServiceRating] = useState(5);
  const [serviceComment, setServiceComment] = useState("");
  const [error, setError] = useState("");
  // "Need help?" opens the request form right here, so closing it lands
  // back on the booking.
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportSubject, setSupportSubject] = useState("");
  const [supportDescription, setSupportDescription] = useState("");
  const [supportError, setSupportError] = useState("");
  const [supportSent, setSupportSent] = useState(false);
  // What the last pay attempt on THIS screen came to.
  const [payNote, setPayNote] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  // When a payment on this screen went through or is still being confirmed:
  // pay actions stay hidden until the server has answered SINCE then (a
  // second payment used to be accepted and parked for a refund).
  const [paymentPendingAt, setPaymentPendingAt] = useState(0);

  const bookingQueryKey = ["booking", id];
  const { data: booking, isLoading, isError, refetch, dataUpdatedAt: bookingUpdatedAt } = useQuery({
    queryKey: bookingQueryKey,
    queryFn: () => bookingApi.get(id as string),
    enabled: !!id,
    // Live-pushed over "booking:{id}" below (status changes only) — this
    // interval is the fallback while the socket reconnects.
    refetchInterval: (query) => (isFinished(query.state.data) ? false : 45000),
  });

  // A car booked as part of a multi-vehicle visit is shown with the rest of it.
  const groupId = booking?.booking_group_id || null;
  const { data: visitBookings } = useQuery({
    queryKey: ["booking-group", groupId],
    queryFn: () => bookingApi.getGroup(groupId as string),
    enabled: !!groupId,
    refetchInterval: (query) => (query.state.data?.every(isFinished) ? false : 45000),
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  // The edit lock (customer_edit_lock_minutes) comes from the live policy.
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get, staleTime: 5 * 60 * 1000 });
  const visit = groupId && visitBookings?.length ? visitBookings : null;
  const live = !!booking && !(visit ? visit.every(isFinished) : isFinished(booking));
  const trailCars = visit || (booking ? [booking] : []);
  const multiCar = trailCars.length > 1;
  // A car cancelled off the visit no longer counts toward its price (a
  // wholly cancelled visit still shows what it came to).
  const liveCars = trailCars.filter((b) => b.status !== "cancelled");
  const pricedCars = liveCars.length ? liveCars : trailCars;
  /** The visit's price — every car still on it. */
  const visitTotal = pricedCars.reduce((sum, b) => sum + (b.total_amount || 0), 0);
  /** What a payment now actually charges — the server's own rule: the
   *  not-cancelled cars' amount_due (total − wallet credit used − paid);
   *  older responses: every unpaid car's total. */
  const dueAmount = visitDue(trailCars);
  const dueCars = liveCars.filter((b) => visitDue([b]) > 0).length;
  // Wallet credit used on this visit, money already received, a previous
  // wallet balance carried in, and services added on site (spec 2026-10-07).
  const money = visitMoney(pricedCars);
  // Charged once per visit (on its first car) and already inside total_amount.
  const travelCharge = pricedCars.reduce((sum, b) => sum + (b.travel_charge || 0), 0);
  const travelKm = trailCars.find((b) => (b.travel_charge || 0) > 0)?.travel_charge_km;
  const prepaidOnly = trailCars.some((b) => b.prepaid_only);
  // A previous late-cancellation charge carried by this visit (in the total).
  const carriedCharge = pricedCars.reduce((sum, b) => sum + (Number(b.cancellation_charge) || 0), 0);
  // Paid online, then cancelled: the money goes back.
  const refundCars = trailCars.filter((b) => b.payment_status === "refund_due" || b.payment_status === "refunded");
  const refundTotal = refundCars.reduce((sum, b) => sum + (Number(b.refunded_amount) || b.total_amount || 0), 0);
  const refundDone = refundCars.length > 0 && refundCars.every((b) => b.payment_status === "refunded");
  // Founder rule: a prepaid service can never fall back to cash.
  const cashAllowed = !prepaidOnly && booking?.status === "awaiting_payment";

  useLiveChannel(id && live ? `booking:${id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: bookingQueryKey });
    if (groupId) queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
  });

  // A failed attempt (retryable) or money received that couldn't be applied.
  const paymentStateKey = ["booking-payment-state", id];
  const { data: paymentState, dataUpdatedAt: paymentStateUpdatedAt } = useQuery({
    queryKey: paymentStateKey,
    queryFn: () => paymentApi.bookingState(id as string),
    enabled: !!id && isCustomer,
    // Faster while a payment is being confirmed, so the page settles by itself.
    refetchInterval: (query) =>
      query.state.data?.confirming || (paymentPendingAt > 0 && query.state.dataUpdatedAt < paymentPendingAt) ? 5000 : live ? 45000 : false,
  });
  /** A payment is in flight or under review — never offer another one. */
  const paymentHeld =
    !!paymentState?.confirming ||
    !!paymentState?.attention ||
    (paymentPendingAt > 0 && (paymentStateUpdatedAt < paymentPendingAt || bookingUpdatedAt < paymentPendingAt));

  // What cancelling now costs (late-cancellation charge, added to the next
  // booking) — read fresh each time the cancel dialog opens.
  const cancelPreview = useQuery({
    queryKey: ["cancellation-charge-preview", id, !!groupId],
    queryFn: (): Promise<CancelPreview> => bookingApi.cancellationChargePreview(id as string, !!groupId),
    enabled: !!id && cancelOpen && isCustomer,
    staleTime: 0,
  });

  const { data: myReviews } = useQuery({ queryKey: ["my-reviews", "for", id], queryFn: () => reviewApi.mine(id ? [id] : undefined), enabled: isCustomer && !!id });
  const myReview = myReviews?.find((r) => r.booking_id === id);

  // A completed-but-unrated booking opens the rating window by itself, once
  // per page open, and only after the reviews query has answered.
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

  // "Edit" from My Bookings lands here with ?edit=1 — open the editor once.
  const editLinkDone = useRef(false);
  useEffect(() => {
    if (editLinkDone.current || searchParams.get("edit") !== "1" || !booking || !isCustomer) return;
    editLinkDone.current = true;
    setEditOpen(true);
  }, [booking, isCustomer, searchParams]);

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
    mutationFn: async (): Promise<CancelOutcome> => {
      if (groupId) return (await bookingApi.cancelGroup(groupId, cancelReason)) as CancelOutcome;
      return (await bookingApi.cancel(id as string, cancelReason)) as CancelOutcome;
    },
    onSuccess: (outcome) => {
      setCancelOutcome(outcome || {});
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
      // A plan wash used on this visit goes back on the pass.
      queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });
      setCancelOpen(false);
      setCancelReason("");
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
          onConfirming: () => {
            setPaymentPendingAt(Date.now());
            setPayNote({ tone: "info", text: "Payment done — confirming it with the bank…" });
          },
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
        setPaymentPendingAt(Date.now());
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
    // One decision for the whole visit.
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

  // Each dialog starts clean — an error from another action (a failed
  // review, a refused reschedule) must not show up inside it.
  const openCancel = () => {
    setError("");
    setCancelOpen(true);
  };

  const supportSubjectValid = supportSubject.trim().length >= 3;
  const supportDescriptionValid = supportDescription.trim().length >= 5;
  const supportMutation = useMutation({
    mutationFn: () => complaintApi.create({ booking_id: id as string, subject: supportSubject.trim(), description: supportDescription.trim() }),
    onSuccess: () => {
      setSupportOpen(false);
      setSupportSubject("");
      setSupportDescription("");
      setSupportError("");
      setSupportSent(true);
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
    },
    onError: (err) => setSupportError(getErrorMessage(err)),
  });

  if (isError && !booking) {
    return (
      <div className="mx-auto max-w-xl space-y-5">
        <PageHeader back="/app/bookings" title="Booking Details" />
        <div className={`${card} p-6 text-center`}>
          <p className="text-sm text-[#5F6878]">Couldn't load this booking.</p>
          <button type="button" className={btn("outline", "sm", "mt-3")} onClick={() => void refetch()}>
            Try Again
          </button>
        </div>
      </div>
    );
  }
  if (isLoading || !booking) return <PageLoader />;

  const slotStartMs = (() => {
    const start = (booking.scheduled_slot || "00:00").split("-")[0].trim();
    const t = new Date(`${booking.scheduled_date.slice(0, 10)}T${start.length === 5 ? start : "00:00"}:00+05:30`).getTime();
    return Number.isNaN(t) ? Date.now() : t;
  })();
  // Spec 2026-10-07: the customer cancels until the captain heads out, and
  // edits until customer_edit_lock_minutes (60) before the slot — the
  // server enforces both; these only decide which buttons show.
  const editLockMinutes = (policy as { customer_edit_lock_minutes?: number } | undefined)?.customer_edit_lock_minutes ?? 60;
  const insideEditLock = Date.now() > slotStartMs - editLockMinutes * 60 * 1000;
  // Not a real booking yet (online payment not finished): nothing to edit — pay or cancel.
  const awaitingPayment = booking.status === "awaiting_payment";
  const failedReason = !payNote && !payMutation.isPending ? paymentState?.last_failure?.reason : undefined;
  const lastFailure = failedReason ? sentence(failedReason) : undefined;
  const captainHeaded = trailCars.some((b) => ["captain_on_the_way", "service_started", "completed"].includes(b.status));
  const bookingOpen = !["completed", "cancelled"].includes(booking.status);
  const canCancel = isCustomer && bookingOpen && (awaitingPayment || !captainHeaded);
  const canEdit = isCustomer && bookingOpen && !awaitingPayment && !captainHeaded && !insideEditLock;
  const cancelLockHint =
    isCustomer && bookingOpen && !canCancel
      ? "Your captain is on the way, so this booking can't be cancelled or changed here. Message us on WhatsApp for help."
      : canCancel && !canEdit && !awaitingPayment
        ? `Changes close ${editLockMinutes >= 60 && editLockMinutes % 60 === 0 ? `${editLockMinutes / 60} hour${editLockMinutes === 60 ? "" : "s"}` : `${editLockMinutes} minutes`} before your slot.`
        : null;
  // Anything still due can be paid online — a cash booking, a part-paid one
  // (an edit or an on-site add-on raised it), even after the wash.
  const canPayOnline =
    isCustomer &&
    booking.status !== "cancelled" &&
    !awaitingPayment &&
    !["refund_due", "refunded"].includes(booking.payment_status) &&
    dueAmount > 0 &&
    !paymentHeld;
  // One payment action at a time: while the checkout is open, nothing else on the visit moves.
  const payBusy = payMutation.isPending || switchToCashMutation.isPending;
  const cancelReasonValid = cancelReason.trim().length >= 3;
  // "Sedan · Star Wash" — the car type and the service, then the make/plate when the booking has them.
  const title = visitTypeServiceLabel({ bookings: trailCars }, vehicleTypes);
  const vehicles = visitCarDetail({ bookings: trailCars }, vehicleTypes);

  // ---- status hero --------------------------------------------------------
  const status = visit ? combinedStatus(visit) : booking.status;
  const day = formatDay(booking.scheduled_date);
  const slot = formatSlot(booking.scheduled_slot);
  const captains = Array.from(new Map(trailCars.filter((b) => b.captain_profile && b.status !== "cancelled").map((b) => [b.captain_profile!.phone || b.captain_profile!.full_name || b.id, b.captain_profile!])).values());
  const captainName = captains[0]?.full_name?.trim().split(/\s+/)[0] || "Your captain";
  const completedAt = trailCars.map((b) => b.completed_at).filter(Boolean).sort().pop();
  const headline: Record<string, [string, string]> = {
    awaiting_payment: ["Payment Pending", "Pay to confirm your slot."],
    pending: ["Booking Confirmed", "We're assigning your captain."],
    rescheduled: ["Booking Rescheduled", "We're assigning your captain."],
    assigned: ["Captain Assigned", `${captainName} will come ${day === "Today" || day === "Tomorrow" ? day.toLowerCase() : `on ${day}`}, ${slot}.`],
    captain_on_the_way: ["Your Captain Is On The Way", `Arriving within your slot · ${slot}`],
    service_started: ["Your Wash Is In Progress", "We'll let you know the moment it's done."],
    completed: ["Wash Complete", completedAt ? `Finished ${stamp(completedAt, booking.scheduled_date.slice(0, 10))}` : "Thanks for washing with Blussit."],
    cancelled: ["Booking Cancelled", booking.cancellation_reason ? `Reason: ${booking.cancellation_reason}` : "This booking won't go ahead."],
  };
  const [heading, subline] = headline[status] || ["Booking", ""];
  const hero = HERO[status] || HERO.pending;
  const HeroIcon = hero.icon;
  const timeline = buildTimeline(trailCars, status);
  const currentIndex = status === "cancelled" ? -1 : timeline.findIndex((s) => !s.done);
  const showCode = !!booking.service_code && !["completed", "cancelled"].includes(booking.status);
  const contactable = !["completed", "cancelled"].includes(status);
  const address = booking.address_snapshot;
  const hasPhotos = trailCars.some((car) => car.before_photo || car.after_photo);

  const heroCard = (
    <section className={`${card} order-1 p-5`}>
      <div className="flex items-start gap-4">
        <span className={cn("flex h-12 w-12 shrink-0 items-center justify-center rounded-full", hero.cls)}>
          <HeroIcon className="h-6 w-6" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="font-display text-[19px] font-bold leading-snug text-[#0E1A33]">{heading}</h2>
          {subline && <p className="mt-0.5 text-sm text-[#5F6878]">{subline}</p>}
        </div>
      </div>
      {showCode && (
        <div className="mt-4 flex items-center justify-between gap-3 rounded-2xl bg-[#F5F8FD] px-4 py-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#0E1A33]">Service Code</p>
            <p className="text-xs text-[#5F6878]">Share it with your captain on arrival.</p>
          </div>
          <p className="tabular-nums text-[26px] font-bold tracking-[0.28em] text-[#0A66F0]">{booking.service_code}</p>
        </div>
      )}
      <div className="mt-4 space-y-2.5 border-t border-[#EEF2F7] pt-4">
        <DetailRow icon={Sparkles}>
          <span className="font-semibold">{title}</span>
        </DetailRow>
        <DetailRow icon={CalendarClock}>
          {day} · {slot}
        </DetailRow>
        {(vehicles || multiCar) && (
          <DetailRow icon={CarFront}>
            {[vehicles, multiCar ? `${trailCars.length} vehicles` : ""].filter(Boolean).join(" · ")}
          </DetailRow>
        )}
        {address?.line1 && (
          <DetailRow icon={MapPin}>
            <span className="text-[#5F6878]">{[address.line1, address.landmark].filter(Boolean).join(", ")}</span>
          </DetailRow>
        )}
      </div>
    </section>
  );

  const captainCards = captains.length > 0 && (
    <section className="order-2 space-y-3">
      {captains.map((c, i) => {
        const wa = whatsAppLink(c.phone);
        return (
          <div key={i} className={`${card} flex items-center gap-4 p-4`}>
            {c.photo_url ? (
              <img src={c.photo_url} alt={c.full_name || "Captain"} className="h-16 w-16 shrink-0 rounded-full object-cover ring-4 ring-[#EEF3FA]" />
            ) : (
              <span className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#E8F0FE] font-display text-xl font-bold text-[#0A66F0]">
                {(c.full_name || "C").trim().charAt(0).toUpperCase()}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-xs text-[#5F6878]">Your Captain</p>
              <p className="truncate text-[17px] font-semibold text-[#0E1A33]">{c.full_name || "Captain"}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-[#5F6878]">
                {c.verified && (
                  <span className="inline-flex items-center gap-0.5 rounded-full bg-[#E7F6EC] px-1.5 py-0.5 font-medium text-[#1E7B3C]">
                    <BadgeCheck className="h-3 w-3" /> Verified
                  </span>
                )}
                {c.employee_id && <span className="tabular-nums">ID {c.employee_id}</span>}
              </p>
            </div>
            {contactable && c.phone && (
              <div className="flex shrink-0 gap-2">
                {wa && (
                  <a href={wa} target="_blank" rel="noopener noreferrer" aria-label="WhatsApp your captain" className="flex h-11 w-11 items-center justify-center rounded-full bg-[#E7F6EC] text-[#1FA855] transition-colors hover:bg-[#D5F0DF]">
                    <WhatsAppGlyph />
                  </a>
                )}
                <a href={`tel:${c.phone}`} aria-label="Call your captain" className="flex h-11 w-11 items-center justify-center rounded-full bg-[#E8F0FE] text-[#0A66F0] transition-colors hover:bg-[#DCE8FD]">
                  <Phone className="h-5 w-5" />
                </a>
              </div>
            )}
          </div>
        );
      })}
    </section>
  );

  const actionsCard = (
    <section className={`${card} order-3 space-y-3 p-4`}>
      {awaitingPayment ? (
        // Not booked until paid: finish paying, or (unless prepaid only) have the captain collect cash.
        <div>
          <p className="font-semibold text-[#0E1A33]">{paymentHeld && !paymentState?.attention ? "Confirming Your Payment…" : paymentState?.attention ? "Payment Under Review" : lastFailure ? "Payment Failed" : "Payment Pending"}</p>
          <p className="mt-1 text-sm text-[#5F6878]">
            {paymentHeld
              ? "No need to pay again — this page updates by itself once it's confirmed."
              : lastFailure
                ? `${lastFailure} Try again${cashAllowed ? ", or pay cash instead" : ""} — unpaid slots are released shortly.`
                : `Pay to confirm your slot${dueCars > 1 ? ` — one payment covers ${dueCars === trailCars.length ? `all ${dueCars}` : dueCars} vehicles` : ""}. Unpaid slots are released shortly.`}
          </p>
          {payNote && <p className={`mt-2 text-sm ${payNote.tone === "error" ? "text-[#C62828]" : "text-[#0E1A33]"}`}>{payNote.text}</p>}
          <div className="mt-4 grid gap-2">
            {!paymentHeld && (
              <button type="button" className={btn("primary", "md", "w-full")} disabled={payBusy} onClick={() => payMutation.mutate()}>
                {payMutation.isPending ? "Opening Payment…" : lastFailure ? `Try Again — ₹${Math.round(dueAmount)}` : `Pay ₹${Math.round(dueAmount)} Now`}
              </button>
            )}
            {!prepaidOnly && !paymentHeld && (
              <button type="button" className={btn("outline", "md", "w-full")} disabled={payBusy} onClick={() => switchToCashMutation.mutate()}>
                {switchToCashMutation.isPending ? "Switching To Cash…" : "Pay Cash Instead"}
              </button>
            )}
            {canCancel && (
              <button type="button" className={btn("danger", "md", "w-full")} disabled={payBusy || paymentHeld} onClick={openCancel}>
                <CalendarX2 className="h-4 w-4" /> Cancel Booking
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="grid gap-2">
          {/* Founder rule: any unpaid booking can be paid online at any time, even one booked as cash. */}
          {canPayOnline && (
            <button type="button" className={btn("primary", "md", "w-full")} disabled={payMutation.isPending} onClick={() => payMutation.mutate()}>
              {payMutation.isPending ? "Opening Payment…" : `Pay ₹${Math.round(dueAmount)} Now`}
            </button>
          )}
          {isCustomer && paymentHeld && !payNote && booking.payment_status !== "paid" && !paymentState?.attention && (
            <p className="text-sm text-[#0E1A33]">Confirming your payment — no need to pay again.</p>
          )}
          {payNote && <p className={`text-sm ${payNote.tone === "error" ? "text-[#C62828]" : "text-[#0E1A33]"}`}>{payNote.text}</p>}
          {isCustomer && ["completed", "cancelled"].includes(booking.status) && (
            <button type="button" className={btn("primary", "md", "w-full")} onClick={() => navigate(`/app/book?repeat=${booking.id}`)}>
              <RotateCcw className="h-4 w-4" /> Book Again
            </button>
          )}
          {isCustomer && booking.status === "completed" && !myReview && (
            <button type="button" className={btn("outline", "md", "w-full")} onClick={openReview}>
              <Star className="h-4 w-4" /> Rate This Wash
            </button>
          )}
          {canEdit && (
            <button
              type="button"
              className={btn("outline", "md", "w-full")}
              data-testid="edit-booking"
              onClick={() => {
                setError("");
                setEditOpen(true);
              }}
            >
              <Pencil className="h-4 w-4" /> Edit Booking
            </button>
          )}
          {canCancel && (
            <button type="button" className={btn("danger", "md", "w-full")} disabled={payBusy} onClick={openCancel}>
              <CalendarX2 className="h-4 w-4" /> Cancel Booking
            </button>
          )}
        </div>
      )}
      {error && <p className="text-sm text-[#C62828]">{error}</p>}
      {cancelLockHint && <p className="text-xs text-[#5F6878]">{cancelLockHint}</p>}
      {supportSent ? (
        <p className="flex items-center gap-1.5 text-sm text-[#1E7B3C]">
          <Check className="h-4 w-4" /> Request sent — we'll get back to you.{" "}
          <Link to="/app/support" className="font-semibold underline underline-offset-2">
            View
          </Link>
        </p>
      ) : (
        <button
          type="button"
          className={btn("ghost", "md", "w-full")}
          onClick={() => {
            setSupportError("");
            setSupportOpen(true);
          }}
        >
          <LifeBuoy className="h-4 w-4" /> Need Help?
        </button>
      )}
    </section>
  );

  const timelineCard = (
    <section className={`${card} order-4 p-5`}>
      <h2 className="mb-4 font-display text-[16px] font-bold text-[#0E1A33]">Status</h2>
      <ol>
        {timeline.map((s, i) => {
          const isLast = i === timeline.length - 1;
          const current = i === currentIndex;
          const lineDone = s.done && timeline[i + 1]?.done;
          return (
            <li key={s.key} className="flex gap-3.5">
              <div className="flex flex-col items-center">
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2",
                    s.tone === "error"
                      ? "border-[#C62828] bg-[#C62828] text-white"
                      : s.done
                        ? "border-[#0A66F0] bg-[#0A66F0] text-white"
                        : current
                          ? "border-[#0A66F0] bg-white ring-4 ring-[#E8F0FE]"
                          : "border-[#D5DCE6] bg-white"
                  )}
                >
                  {s.tone === "error" ? <Ban className="h-3.5 w-3.5" /> : s.done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : current ? <span className="h-2 w-2 rounded-full bg-[#0A66F0]" /> : null}
                </span>
                {!isLast && <span className={cn("my-1 w-0.5 flex-1 rounded-full", lineDone ? "bg-[#0A66F0]" : "bg-[#E4E9F1]")} />}
              </div>
              <div className={cn("min-w-0 flex-1", isLast ? "pb-0" : "pb-5")}>
                <p className={cn("text-sm font-semibold", s.tone === "error" ? "text-[#C62828]" : s.done || current ? "text-[#0E1A33]" : "text-[#8A94A6]")}>{s.label}</p>
                <p className="mt-0.5 min-h-4 tabular-nums text-xs text-[#5F6878]">{s.at || (current ? "Up Next" : "")}</p>
              </div>
            </li>
          );
        })}
      </ol>
      {multiCar && <p className="mt-4 text-xs text-[#5F6878]">One timeline for all {trailCars.length} vehicles — it completes when the last one is done.</p>}
    </section>
  );

  const priceCard = (
    <section className={`${card} order-5 p-5`}>
      <h2 className="mb-3 font-display text-[16px] font-bold text-[#0E1A33]">Price Details</h2>
      <div className="space-y-2.5 text-sm">
        {visit ? (
          visit.map((car) => {
            // A car cancelled off a still-live visit isn't charged: shown struck, out of the total.
            const dropped = car.status === "cancelled" && liveCars.length > 0;
            return (
              <div key={car.id} className="flex justify-between gap-3">
                <span className="min-w-0 text-[#5F6878]">
                  <span className="block font-medium text-[#0E1A33]">{visitTypeServiceLabel({ bookings: [car] }, vehicleTypes)}</span>
                  {[visitCarDetail({ bookings: [car] }, vehicleTypes), dropped ? "Cancelled" : ""].filter(Boolean).join(" · ")}
                  {((car as BookingMoney).added_services || []).map((a, i) => (
                    <span key={i} className="block text-xs">
                      Incl. {toTitle(a.name) || "Extra Service"} ₹{Math.round(a.amount || 0)} — {addedByLine(a)}
                    </span>
                  ))}
                </span>
                <span className={cn("tabular-nums", dropped ? "text-[#8A94A6] line-through" : "text-[#0E1A33]")}>
                  ₹{Math.round((car.total_amount || 0) - (dropped ? 0 : (car.travel_charge || 0) + (Number(car.cancellation_charge) || 0) + (Number((car as BookingMoney).wallet_due_carried) || 0)))}
                </span>
              </div>
            );
          })
        ) : (
          <>
            <div className="flex justify-between">
              <span className="text-[#5F6878]">Subtotal</span>
              <span className="tabular-nums text-[#0E1A33]">₹{Math.round(booking.subtotal)}</span>
            </div>
            {booking.discount_amount > 0 && (
              <div className="flex justify-between text-[#1E7B3C]">
                <span>Discount</span>
                <span className="tabular-nums">-₹{Math.round(booking.discount_amount)}</span>
              </div>
            )}
          </>
        )}
        {travelCharge > 0 && (
          <div className="flex justify-between">
            <span className="text-[#5F6878]">Distance Charge{travelKm ? ` · ${Math.round(travelKm * 10) / 10} km` : ""}</span>
            <span className="tabular-nums text-[#0E1A33]">₹{Math.round(travelCharge)}</span>
          </div>
        )}
        {!visit && money.addedServices > 0 && (
          <div className="space-y-1.5" data-testid="booking-added-services">
            {((booking as BookingMoney).added_services || []).length ? (
              ((booking as BookingMoney).added_services || []).map((a, i) => (
                <div key={i} className="flex justify-between gap-3">
                  <span className="min-w-0 text-[#5F6878]">
                    {toTitle(a.name) || "Extra Service"}
                    {(a.qty || 1) > 1 ? ` ×${a.qty}` : ""} <span className="text-xs">— {addedByLine(a)}</span>
                  </span>
                  <span className="tabular-nums text-[#0E1A33]">₹{Math.round(a.amount || 0)}</span>
                </div>
              ))
            ) : (
              <div className="flex justify-between gap-3">
                <span className="text-[#5F6878]">Added On Site</span>
                <span className="tabular-nums text-[#0E1A33]">₹{Math.round(money.addedServices)}</span>
              </div>
            )}
          </div>
        )}
        {money.walletCarried > 0 && (
          <div className="flex justify-between gap-3" data-testid="booking-previous-balance">
            <span className="text-[#5F6878]">Previous Balance Due</span>
            <span className="tabular-nums text-[#0E1A33]">₹{Math.round(money.walletCarried)}</span>
          </div>
        )}
        {carriedCharge > 0 && (
          <div className="flex justify-between gap-3" data-testid="booking-carried-charge">
            <span className="text-[#5F6878]">Previous Cancellation Charge</span>
            <span className="tabular-nums text-[#0E1A33]">₹{Math.round(carriedCharge)}</span>
          </div>
        )}
        <div className="flex justify-between border-t border-[#EEF2F7] pt-3 text-base font-bold text-[#0E1A33]">
          <span>Total</span>
          <span className="tabular-nums">₹{Math.round(visitTotal)}</span>
        </div>
        {money.walletApplied > 0 && (
          <div className="flex justify-between gap-3 text-[#1E7B3C]" data-testid="booking-wallet-applied">
            <span>Wallet Credit</span>
            <span className="tabular-nums">−₹{Math.round(money.walletApplied)}</span>
          </div>
        )}
        {money.amountPaid > 0 && (
          <div className="flex justify-between gap-3" data-testid="booking-amount-paid">
            <span className="text-[#5F6878]">Paid</span>
            <span className="tabular-nums text-[#0E1A33]">₹{Math.round(money.amountPaid)}</span>
          </div>
        )}
        {dueAmount > 0 && (Math.round(dueAmount) !== Math.round(visitTotal) || money.walletApplied > 0 || money.amountPaid > 0) && (
          <div className="flex justify-between rounded-xl bg-[#FFF8EE] px-3 py-2 text-sm font-bold text-[#0E1A33]" data-testid="booking-amount-due">
            <span>Amount Due</span>
            <span className="tabular-nums">₹{Math.round(dueAmount)}</span>
          </div>
        )}
        <div className="flex justify-between text-xs text-[#5F6878]">
          <span>Payment</span>
          <span>
            {PAYMENT_METHOD_LABELS[booking.payment_method] || (booking.payment_method || "—").replace(/_/g, " ")}
            {["paid", "partially_paid"].includes(booking.payment_status) ? ` · ${paymentStatusLabel(booking.payment_status)}` : ""}
          </span>
        </div>
        {(refundCars.length > 0 || paymentState?.refund) && (
          <div className="flex justify-between gap-3 rounded-xl bg-[#F4F8FF] px-3 py-2 text-sm font-semibold text-[#0E1A33]" data-testid="booking-refund">
            {(paymentState?.refund?.status ?? (refundDone ? "refunded" : "due")) === "refunded" ? (
              <>
                <span>Refunded</span>
                <span className="tabular-nums">₹{Math.round(paymentState?.refund?.amount ?? refundTotal)}</span>
              </>
            ) : (
              <>
                <span>Refund In Process</span>
                <span className="tabular-nums">₹{Math.round(paymentState?.refund?.amount ?? refundTotal)}</span>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );

  const photosCard = hasPhotos && (
    <section className={`${card} order-6 p-5`}>
      <h2 className="mb-4 font-display text-[16px] font-bold text-[#0E1A33]">Before & After</h2>
      <div className="space-y-5">
        {trailCars.map(
          (car) =>
            (car.before_photo || car.after_photo) && (
              <div key={car.id}>
                {multiCar && <p className="mb-2 text-xs font-semibold text-[#0E1A33]">{[visitTypeServiceLabel({ bookings: [car] }, vehicleTypes), visitCarDetail({ bookings: [car] }, vehicleTypes)].filter(Boolean).join(" · ")}</p>}
                <div className="grid grid-cols-2 gap-3">
                  {car.before_photo && (
                    <figure>
                      <img src={car.before_photo.image_url} alt="Before service" loading="lazy" className="aspect-[4/3] w-full rounded-xl object-cover" />
                      <figcaption className="mt-1.5 text-xs text-[#5F6878]">Before · {stamp(car.before_photo.captured_at, car.scheduled_date.slice(0, 10))}</figcaption>
                    </figure>
                  )}
                  {car.after_photo && (
                    <figure>
                      <img src={car.after_photo.image_url} alt="After service" loading="lazy" className="aspect-[4/3] w-full rounded-xl object-cover" />
                      <figcaption className="mt-1.5 text-xs text-[#5F6878]">After · {stamp(car.after_photo.captured_at, car.scheduled_date.slice(0, 10))}</figcaption>
                    </figure>
                  )}
                </div>
              </div>
            )
        )}
      </div>
    </section>
  );

  const reviewCard = isCustomer && myReview && (
    <section className={`${card} order-7 p-4`}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[#0E1A33]">Your Rating</p>
          <span className="mt-1 flex items-center gap-0.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Star key={i} className={`h-4 w-4 ${i < (myReview.service_rating ?? 0) ? "fill-[#FFB800] text-[#FFB800]" : "text-[#D5DCE6]"}`} />
            ))}
          </span>
        </div>
        <div className="flex gap-1.5">
          <button type="button" className={btn("outline", "sm")} onClick={openReview}>
            Edit
          </button>
          <button
            type="button"
            className={btn("ghost", "sm")}
            disabled={deleteReviewMutation.isPending}
            onClick={async () => {
              if (await confirm({ title: "Delete Your Review?", message: "It can't be restored afterwards.", tone: "danger" })) deleteReviewMutation.mutate();
            }}
          >
            Delete
          </button>
        </div>
      </div>
    </section>
  );

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        back="/app/bookings"
        title="Booking Details"
        subtitle={
          <span className="tabular-nums">
            {visit ? visit.map((b) => b.booking_number).join(" · ") : booking.booking_number}
          </span>
        }
        right={<span className="hidden sm:block"><StatusChip status={status} /></span>}
      />

      {/* Money that arrived but couldn't be applied — said plainly, so nobody pays twice. */}
      {isCustomer && paymentState?.attention && (
        <div className="rounded-2xl border border-[#FFD8A8] bg-[#FFF8EE] p-4">
          <p className="font-semibold text-[#0E1A33]">Payment Received — Under Review</p>
          <p className="mt-1 text-sm text-[#5F6878]">
            ₹{Math.round(paymentState.attention.amount)} · {paymentState.attention.message}
          </p>
        </div>
      )}

      {/* Phones: one column in reading order (order-*). From 1024px: the
          story on the left, money and actions on the right. */}
      <div className="flex flex-col gap-5 lg:grid lg:grid-cols-5 lg:items-start">
        <div className="contents lg:col-span-3 lg:block lg:space-y-5">
          {heroCard}
          {captainCards}
          {timelineCard}
          {photosCard}
        </div>
        <div className="contents lg:col-span-2 lg:block lg:space-y-5">
          {actionsCard}
          {priceCard}
          {reviewCard}
        </div>
      </div>

      <Modal open={cancelOpen} onClose={() => setCancelOpen(false)} title="Cancel Booking">
        <div className="space-y-4">
          <div className="space-y-1.5 rounded-xl bg-[#F4F8FF] px-3.5 py-3 text-sm text-[#0E1A33]" data-testid="customer-cancel-preview">
            {cancelPreview.isLoading ? (
              <span className="text-[#5F6878]">Checking what cancelling costs…</span>
            ) : cancelPreview.data ? (
              cancelPreview.data.can_cancel === false ? (
                <span>{cancelPreview.data.refusal || "This booking can't be cancelled now."}</span>
              ) : (
                <>
                  {cancelPreview.data.amount > 0 ? <p className="font-semibold">Charge ₹{Math.round(cancelPreview.data.amount)}</p> : null}
                  {(cancelPreview.data.net ?? 0) > 0 && <p className="font-semibold text-[#1E7B3C]">₹{Math.round(cancelPreview.data.net ?? 0)} Back To Your Wallet</p>}
                  {(cancelPreview.data.net ?? 0) < 0 && <p className="font-semibold">₹{Math.round(-(cancelPreview.data.net ?? 0))} Added To Your Next Booking</p>}
                  {(cancelPreview.data.plan_wash_forfeited_count ?? 0) > 0 ? (
                    <p className="font-semibold text-[#B25E00]">This Plan Wash Will Be Used Up</p>
                  ) : cancelPreview.data.plan_wash_returned ? (
                    <p className="font-semibold">Your Plan Wash Will Be Returned</p>
                  ) : null}
                  {!(cancelPreview.data.amount > 0) && !(cancelPreview.data.net ?? 0) && !(cancelPreview.data.plan_wash_forfeited_count ?? 0) && !cancelPreview.data.plan_wash_returned && (
                    <p className="font-semibold">Cancelling now is free.</p>
                  )}
                </>
              )
            ) : (
              <span className="text-[#5F6878]">Free up to 4 hours before your slot.</span>
            )}
            <Link to="/cancellation-policy" className="block text-xs font-semibold text-[#0A66F0] underline-offset-2 hover:underline">
              Cancellation Policy
            </Link>
          </div>
          {cancelPreview.data?.can_cancel === false ? (
            <div className="grid gap-2">
              <button type="button" className={btn("outline", "md", "w-full")} onClick={() => openBlussitWhatsApp(`Hi Blussit, I need help with booking ${booking.booking_number}.`)}>
                <WhatsAppGlyph className="h-[18px] w-[18px]" /> Message Us On WhatsApp
              </button>
              <a href="tel:8962288774" className={btn("ghost", "md", "w-full")}>
                <Phone className="h-4 w-4" /> Call Us
              </a>
            </div>
          ) : (
            <>
          <Input
            label="Reason For Cancellation"
            value={cancelReason}
            onChange={(e) => setCancelReason(e.target.value)}
            hint={!cancelReasonValid ? "At least 3 characters, so we know why." : undefined}
          />
          {error && <p className="text-sm text-[#C62828]">{error}</p>}
          <button type="button" className={btn("primary", "md", "w-full bg-[#C62828] hover:bg-[#B71C1C]")} disabled={!cancelReasonValid || cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>
            {cancelMutation.isPending ? "Cancelling…" : "Confirm Cancellation"}
          </button>
            </>
          )}
        </div>
      </Modal>

      <Modal open={!!cancelOutcome} onClose={() => setCancelOutcome(null)} title="Booking Cancelled">
        <div className="space-y-3 text-sm text-[#0E1A33]" data-testid="cancel-outcome">
          {cancelOutcome?.wallet?.wallet_line ? <p>{cancelOutcome.wallet.wallet_line}</p> : <p>Your booking is cancelled.</p>}
          {!!cancelOutcome?.plan_wash_forfeited && <p className="font-semibold text-[#B25E00]">The plan wash on this booking is used up.</p>}
          <div className="grid gap-2 pt-1">
            {cancelOutcome?.wallet && (cancelOutcome.wallet.net ?? 0) !== 0 && (
              <Link to="/app/wallet" className={btn("outline", "md", "w-full")}>
                View My Wallet
              </Link>
            )}
            <button type="button" className={btn("primary", "md", "w-full")} onClick={() => setCancelOutcome(null)}>
              Done
            </button>
          </div>
        </div>
      </Modal>

      <EditBookingSheet
        open={editOpen}
        onClose={() => setEditOpen(false)}
        cars={liveCars.length ? liveCars : trailCars}
        groupId={groupId}
        vehicleTypes={vehicleTypes}
        onSaved={(result) => {
          setEditOpen(false);
          setEditResult(result);
          queryClient.invalidateQueries({ queryKey: bookingQueryKey });
          queryClient.invalidateQueries({ queryKey: ["booking-group", groupId] });
          queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
          queryClient.invalidateQueries({ queryKey: ["available-slots"] });
          queryClient.invalidateQueries({ queryKey: ["my-wallet"] });
          queryClient.invalidateQueries({ queryKey: paymentStateKey });
        }}
      />
      <EditResultSheet
        result={editResult}
        onClose={() => setEditResult(null)}
        onPay={
          isCustomer && !paymentHeld
            ? () => {
                setEditResult(null);
                payMutation.mutate();
              }
            : undefined
        }
      />


      <Modal open={reviewOpen} onClose={() => setReviewOpen(false)} title={myReview ? "Edit Your Review" : "Rate Your Wash"}>
        <div className="space-y-4">
          {booking.captain_id && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[#0E1A33]">Captain</p>
              <div className="flex gap-1">
                {[1, 2, 3, 4, 5].map((r) => (
                  <button key={r} type="button" aria-label={`${r} star${r > 1 ? "s" : ""}`} onClick={() => setCaptainRating(r)}>
                    <Star className={`h-7 w-7 ${r <= captainRating ? "fill-[#FFB800] text-[#FFB800]" : "text-[#D5DCE6]"}`} />
                  </button>
                ))}
              </div>
              <Input className="mt-2" placeholder="Comment on your captain (optional)" value={captainComment} onChange={(e) => setCaptainComment(e.target.value)} />
            </div>
          )}
          <div>
            <p className="mb-1.5 text-sm font-medium text-[#0E1A33]">Service Quality</p>
            <div className="flex gap-1">
              {[1, 2, 3, 4, 5].map((r) => (
                <button key={r} type="button" aria-label={`${r} star${r > 1 ? "s" : ""}`} onClick={() => setServiceRating(r)}>
                  <Star className={`h-7 w-7 ${r <= serviceRating ? "fill-[#FFB800] text-[#FFB800]" : "text-[#D5DCE6]"}`} />
                </button>
              ))}
            </div>
            <Input className="mt-2" placeholder="Comment on the service (optional)" value={serviceComment} onChange={(e) => setServiceComment(e.target.value)} />
          </div>
          {error && <p className="text-sm text-[#C62828]">{error}</p>}
          <button type="button" className={btn("primary", "md", "w-full")} disabled={reviewMutation.isPending} onClick={() => reviewMutation.mutate()}>
            {reviewMutation.isPending ? "Saving…" : myReview ? "Save Changes" : "Submit Review"}
          </button>
        </div>
      </Modal>

      <Modal open={supportOpen} onClose={() => setSupportOpen(false)} title="Raise A Support Request">
        <div className="space-y-4">
          <p className="text-xs text-[#5F6878]">
            Booking <span className="tabular-nums">{booking.booking_number}</span>
          </p>
          <Input label="What Went Wrong?" value={supportSubject} onChange={(e) => setSupportSubject(e.target.value)} hint={!supportSubjectValid ? "At least 3 characters." : undefined} />
          <Input label="Tell Us A Bit More" value={supportDescription} onChange={(e) => setSupportDescription(e.target.value)} hint={!supportDescriptionValid ? "At least 5 characters." : undefined} />
          {supportError && <p className="text-sm text-[#C62828]">{supportError}</p>}
          <button
            type="button"
            className={btn("primary", "md", "w-full")}
            disabled={!supportSubjectValid || !supportDescriptionValid || supportMutation.isPending}
            onClick={() => supportMutation.mutate()}
          >
            {supportMutation.isPending ? "Sending…" : "Submit Request"}
          </button>
        </div>
      </Modal>
    </div>
  );
}
