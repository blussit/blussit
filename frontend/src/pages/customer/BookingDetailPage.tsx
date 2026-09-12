import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { BadgeCheck, CheckCircle2, ChevronLeft, Clock, LifeBuoy, MapPin, Navigation, Pencil, Phone, RotateCcw, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { reviewApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { Button, Card, CardBody, CardHeader, Input, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { SlotPicker } from "../../components/shared/SlotPicker";
import { useLiveChannel } from "../../lib/socket";
import { formatDateTime } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";

export default function BookingDetailPage() {
  const { id } = useParams<{ id: string }>();
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

  const bookingQueryKey = ["booking", id];
  const { data: booking, isLoading } = useQuery({
    queryKey: bookingQueryKey,
    queryFn: () => bookingApi.get(id as string),
    enabled: !!id,
    // Live-pushed over "booking:{id}" below (status/assignment/priority
    // changes) — this interval is the fallback for while the socket is
    // reconnecting, not the primary way this page stays current.
    refetchInterval: 45000,
  });

  // A car booked as part of a multi-vehicle visit is only half the story on
  // its own — the customer booked ONE thing and expects to see all of it.
  const groupId = booking?.booking_group_id || null;
  const { data: visitBookings } = useQuery({
    queryKey: ["booking-group", groupId],
    queryFn: () => bookingApi.getGroup(groupId as string),
    enabled: !!groupId,
    refetchInterval: 45000,
  });
  const visit = groupId && visitBookings?.length ? visitBookings : null;
  /** The whole visit's outstanding amount — what the customer actually owes. */
  const visitTotal = (visit || (booking ? [booking] : [])).reduce((sum, b) => sum + (b.total_amount || 0), 0);

  useLiveChannel(id ? `booking:${id}` : null, () => {
    queryClient.invalidateQueries({ queryKey: bookingQueryKey });
  });

  const { data: myReviews } = useQuery({ queryKey: ["my-reviews"], queryFn: reviewApi.mine, enabled: isCustomer });
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
    if (autoOpenedRef.current || !isCustomer) return;
    if (booking?.status !== "completed" || myReviews === undefined || myReview) return;
    autoOpenedRef.current = true;
    openReview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booking?.status, myReviews]);

  const openReview = () => {
    setError("");
    if (myReview) {
      setCaptainRating(myReview.captain_rating);
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
    mutationFn: () =>
      myReview
        ? reviewApi.update(myReview.id, { captain_rating: captainRating, captain_comment: captainComment, service_rating: serviceRating, service_comment: serviceComment })
        : reviewApi.create({ booking_id: id as string, captain_rating: captainRating, captain_comment: captainComment, service_rating: serviceRating, service_comment: serviceComment }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      queryClient.invalidateQueries({ queryKey: ["my-reviews"] });
      setReviewOpen(false);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const payMutation = useMutation({
    mutationFn: () =>
      payWithRazorpay(
        { purpose: "booking", booking_id: id as string },
        { name: user?.full_name, email: user?.email, contact: user?.phone }
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: bookingQueryKey });
      queryClient.invalidateQueries({ queryKey: ["my-bookings"] });
    },
    onError: (err) => {
      if (err instanceof PaymentCancelled) return; // closed the modal — no error to show
      setError(getErrorMessage(err));
    },
  });

  const switchToCashMutation = useMutation({
    mutationFn: () => bookingApi.switchToCash(id as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: bookingQueryKey });
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
  const canCancel = isCustomer && (awaitingPayment || (unassigned && !insideCancelLock));
  const cancelLockHint =
    isCustomer && !["completed", "cancelled"].includes(booking.status) && !canCancel
      ? !unassigned
        ? "A captain is on this booking — it can no longer be cancelled online."
        : "Cancellations close 4 hours before your slot — message us on WhatsApp if you need help."
      : null;
  // Mirrors the backend's reschedule guard — once a captain is on the way
  // or mid-service, rescheduling would pull the booking out from under
  // real, unfinished work with no notice; cancel or wait it out instead.
  const canReschedule =
    isCustomer &&
    !["completed", "cancelled", "captain_on_the_way", "service_started", "awaiting_payment"].includes(booking.status);
  const cancelReasonValid = cancelReason.trim().length >= 3;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(-1)} className="rounded-full p-2 hover:bg-gray-100">
          <ChevronLeft className="h-5 w-5" />
        </button>
        <div>
          <h1 className="font-mono-num text-xl font-bold text-[var(--color-text-primary)]">{booking.booking_number}</h1>
          <p className="text-sm text-[var(--color-text-secondary)]">{formatDateTime(booking.created_at)}</p>
        </div>
        <StatusBadge status={booking.status} />
      </div>

      {/* The whole point of the awaiting-payment state: say plainly that
          this isn't booked yet, and give the two ways out — finish paying,
          or have the captain collect cash instead. */}
      {isCustomer && awaitingPayment && (
        <Card className="border-2 border-[#E8A900] bg-[#FFFCF0]">
          <CardBody className="!p-5">
            <p className="font-display text-base font-bold text-black">Payment not completed</p>
            <p className="mt-1 text-sm text-gray-600">
              We're holding your {booking.scheduled_slot} slot, but {visit ? "this visit isn't" : "this booking isn't"}{" "}
              confirmed until the payment goes through. Finish paying, or have the captain collect the cash at your
              doorstep.
              {visit ? ` One payment covers all ${visit.length} vehicles.` : ""}
            </p>
            <div className="mt-4 flex flex-wrap gap-2.5">
              <Button
                isLoading={payMutation.isPending}
                onClick={() => payMutation.mutate()}
                className="bg-[#E8A900] hover:bg-[#D99A00]"
              >
                Pay ₹{visitTotal} online
              </Button>
              <Button
                variant="outline"
                isLoading={switchToCashMutation.isPending}
                onClick={() => switchToCashMutation.mutate()}
              >
                Pay cash on service instead
              </Button>
              {/* Nothing is committed yet, so everything is still open —
                  the wizard reopens with every choice filled in. */}
              <Button variant="ghost" onClick={() => navigate(`/app/book?edit=${booking.id}`)}>
                <Pencil className="h-4 w-4" /> Edit booking
              </Button>
            </div>
            <p className="mt-3 text-xs text-gray-400">
              If you do neither, we'll release the slot shortly so someone else can book it.
            </p>
          </CardBody>
        </Card>
      )}

      {/* Who's coming to your door — the captain's public card, shown the
          moment one is assigned (photo, staff id, phone). */}
      {booking.captain_profile && booking.status !== "cancelled" && (
        <Card className="border-[#F3E5B5]">
          <CardBody className="flex items-center gap-4 !p-4">
            {booking.captain_profile.photo_url ? (
              <img src={booking.captain_profile.photo_url} alt={booking.captain_profile.full_name || "Captain"} className="h-14 w-14 shrink-0 rounded-full object-cover" />
            ) : (
              <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gray-100 font-display text-lg font-bold text-black">
                {(booking.captain_profile.full_name || "C").trim().charAt(0).toUpperCase()}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-bold uppercase tracking-[0.16em] text-black">Your captain</p>
              <p className="flex flex-wrap items-center gap-1.5 font-semibold text-black">
                {booking.captain_profile.full_name || "Captain"}
                {booking.captain_profile.verified && (
                  <span className="inline-flex items-center gap-0.5 rounded-full bg-green-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-green-700">
                    <BadgeCheck className="h-2.5 w-2.5" /> Verified
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
                className="flex shrink-0 items-center gap-1.5 rounded-xl bg-black px-3.5 py-2.5 text-sm font-bold text-white hover:opacity-90"
              >
                <Phone className="h-4 w-4" /> Call
              </a>
            )}
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader>
          <h2 className="font-semibold text-[var(--color-text-primary)]">Booking summary</h2>
        </CardHeader>
        <CardBody className="space-y-3 text-sm">
          <div className="flex justify-between">
            <span className="text-[var(--color-text-secondary)]">Scheduled for</span>
            <span>{new Date(booking.scheduled_date).toLocaleDateString("en-IN")} · {booking.scheduled_slot}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--color-text-secondary)]">Payment method</span>
            <span className="capitalize">{booking.payment_method.replace(/_/g, " ")}</span>
          </div>

          {/* A visit is one booking to the customer, so the summary shows
              every vehicle on it — not just whichever car's page they
              happened to open. */}
          {visit ? (
            <div className="space-y-2 border-t border-gray-100 pt-3">
              {visit.map((car) => (
                <div key={car.id}>
                  <p className="flex items-center gap-2 text-xs font-semibold text-black">
                    {car.vehicle_snapshot
                      ? `${car.vehicle_snapshot.brand} ${car.vehicle_snapshot.model} · ${car.vehicle_snapshot.registration_number}`
                      : "Vehicle"}
                    {car.id === booking.id && (
                      <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] font-bold uppercase text-gray-600">
                        this page
                      </span>
                    )}
                  </p>
                  <div className="flex justify-between">
                    <span className="text-[var(--color-text-secondary)]">
                      {car.combo_name || car.service_names?.join(", ") || "Service"}
                      <span className="font-mono-num ml-1.5 text-xs text-gray-400">{car.booking_number}</span>
                    </span>
                    <span className="font-mono-num">₹{car.total_amount}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <>
              <div className="flex justify-between">
                <span className="text-[var(--color-text-secondary)]">Subtotal</span>
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
          <div className="flex justify-between border-t border-gray-100 pt-3 text-base font-bold">
            <span>Total{visit ? ` · ${visit.length} vehicles` : ""}</span>
            <span className="font-mono-num">₹{visitTotal}</span>
          </div>
        </CardBody>
      </Card>

      {!!booking.status_history?.length && (
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Status timeline</h2>
          </CardHeader>
          <CardBody>
            <div className="space-y-4">
              {booking.status_history.map((h, i) => (
                <div key={i} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    </span>
                    {i < (booking.status_history?.length || 0) - 1 && <div className="mt-1 h-full w-px flex-1 bg-gray-200" />}
                  </div>
                  <div className="pb-4">
                    <p className="text-sm font-medium capitalize text-[var(--color-text-primary)]">{h.status.replace(/_/g, " ")}</p>
                    {h.note && <p className="text-xs text-[var(--color-text-secondary)]">{h.note}</p>}
                    <p className="mt-0.5 text-xs text-gray-400">{formatDateTime(h.created_at)}</p>
                  </div>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Issue flags ("Captain started late", geofence alerts…) are
          internal manager↔captain ops — the API redacts them for customer
          responses (_CUSTOMER_HIDDEN_FIELDS) and no banner renders here.
          The customer's window into progress is the transparency timeline
          below, which shows what happened, not who got flagged. */}

      {/* Internal to manager/captain — the specific lateness stage and pay
          penalty are operational/financial detail, not something the
          customer needs (they already see the generic "flagged" card
          above if there's an active issue). */}
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

      {(booking.heading_at || booking.before_photo || booking.after_photo) && (
        <Card>
          <CardHeader>
            <h2 className="font-semibold text-[var(--color-text-primary)]">Service transparency trail</h2>
          </CardHeader>
          <CardBody className="space-y-5">
            {booking.heading_at && (
              <div className="flex items-start gap-3 text-sm">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                  <Navigation className="h-4 w-4" />
                </span>
                <div>
                  <p className="font-medium text-[var(--color-text-primary)]">Captain started heading over</p>
                  <p className="text-xs text-[var(--color-text-secondary)]">{formatDateTime(booking.heading_at)}</p>
                  {booking.heading_location && (
                    <p className="mt-0.5 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
                      <MapPin className="h-3 w-3" /> {booking.heading_location.latitude.toFixed(5)}, {booking.heading_location.longitude.toFixed(5)}
                    </p>
                  )}
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {booking.before_photo && (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Before service</p>
                  <img src={booking.before_photo.image_url} alt="Before service" className="h-40 w-full rounded-xl object-cover" />
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
                    <MapPin className="h-3 w-3" /> {formatDateTime(booking.before_photo.captured_at)}
                  </p>
                </div>
              )}
              {booking.after_photo && (
                <div>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">After service</p>
                  <img src={booking.after_photo.image_url} alt="After service" className="h-40 w-full rounded-xl object-cover" />
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
                    <MapPin className="h-3 w-3" /> {formatDateTime(booking.after_photo.captured_at)}
                  </p>
                </div>
              )}
            </div>

            {/* Captain payout / platform margin — internal financial detail,
                never shown to the customer or the captain themselves, only
                to manager/admin (this page is shared across all three). */}
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

      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

      <div className="flex flex-wrap gap-3">
        {/* An online booking whose payment didn't go through (modal closed
            mid-checkout, network blip) finishes it from here — same
            server-verified Razorpay flow as at booking time. */}
        {/* Founder rule: ANY unpaid booking can be paid online any time —
            before, during or after the service — even one booked as cash
            (paying flips it to online; the captain's app sees it as paid). */}
        {isCustomer &&
          booking.payment_status === "pending" &&
          booking.status !== "cancelled" &&
          !awaitingPayment && // the panel above already offers this, with context
          booking.total_amount > 0 && (
            <Button
              isLoading={payMutation.isPending}
              onClick={() => payMutation.mutate()}
              className="bg-[#E8A900] hover:bg-[#D99A00]"
            >
              Pay ₹{visitTotal} online
            </Button>
          )}
        {/* Editable while it can still be cancelled online (unassigned and
            outside the 4-hour lock): the wizard reopens with everything
            filled in, and confirming replaces this booking. */}
        {canCancel && !awaitingPayment && (
          <Button variant="outline" onClick={() => navigate(`/app/book?edit=${booking.id}`)}>
            <Pencil className="h-4 w-4" /> Edit booking
          </Button>
        )}
        {canReschedule && (
          <Button variant="outline" onClick={() => setRescheduleOpen(true)}>
            Reschedule
          </Button>
        )}
        {canCancel && (
          <Button variant="danger" onClick={() => setCancelOpen(true)}>
            Cancel booking
          </Button>
        )}
        {isCustomer && (
          <Button variant="outline" onClick={() => navigate(`/app/support?booking=${booking.id}`)}>
            <LifeBuoy className="h-4 w-4" /> Need help?
          </Button>
        )}
        {cancelLockHint && <p className="w-full text-xs text-[var(--color-text-secondary)]">{cancelLockHint}</p>}
        {isCustomer && booking.status === "completed" && !myReview && (
          <Button variant="outline" onClick={openReview}>
            <Star className="h-4 w-4" /> Rate this service
          </Button>
        )}
        {isCustomer && myReview && (
          <>
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
          </>
        )}
        {/* Book the same thing again: the wizard replays the vehicle, the
            services and the address from this booking and asks only for a
            new date and slot. Offered on a finished wash and on a cancelled
            one — a cancellation is the other moment a customer wants
            exactly this booking back, and re-picking it by hand is the
            whole friction. */}
        {isCustomer && ["completed", "cancelled"].includes(booking.status) && (
          <Button
            variant={booking.status === "cancelled" ? "primary" : "outline"}
            onClick={() => navigate(`/app/book?repeat=${booking.id}`)}
          >
            <RotateCcw className="h-4 w-4" /> Book again{visit ? ` · ${visit.length} vehicles` : ""}
          </Button>
        )}
      </div>

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

      <Modal open={rescheduleOpen} onClose={() => setRescheduleOpen(false)} title="Reschedule booking">
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            This clears the current captain — you'll need a new one assigned to the new time.
          </p>
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
    </div>
  );
}
