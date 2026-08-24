import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, ChevronLeft, Clock, MapPin, Navigation, Star } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { reviewApi } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { Button, Card, CardBody, CardHeader, Input, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { formatDateTime, todayIST } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { ISSUE_LABELS } from "../../lib/constants";

export default function BookingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isCustomer = user?.role === "customer";
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [newDate, setNewDate] = useState("");
  const [newTime, setNewTime] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");

  const { data: booking, isLoading } = useQuery({
    queryKey: ["booking", id],
    queryFn: () => bookingApi.get(id as string),
    enabled: !!id,
  });

  const cancelMutation = useMutation({
    mutationFn: () => bookingApi.cancel(id as string, cancelReason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      setCancelOpen(false);
      setCancelReason("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const rescheduleMutation = useMutation({
    mutationFn: () => bookingApi.reschedule(id as string, newDate, newTime),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      setRescheduleOpen(false);
      setNewDate("");
      setNewTime("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const reviewMutation = useMutation({
    mutationFn: () => reviewApi.create({ booking_id: id as string, rating, comment }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["booking", id] });
      setReviewOpen(false);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (isLoading || !booking) return <PageLoader />;

  const canCancel = isCustomer && !["completed", "cancelled"].includes(booking.status);
  // Mirrors the backend's reschedule guard — once a captain is on the way
  // or mid-service, rescheduling would pull the booking out from under
  // real, unfinished work with no notice; cancel or wait it out instead.
  const canReschedule =
    isCustomer && !["completed", "cancelled", "captain_on_the_way", "service_started"].includes(booking.status);
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
          <div className="flex justify-between border-t border-gray-100 pt-3 text-base font-bold">
            <span>Total</span>
            <span className="font-mono-num">₹{booking.total_amount}</span>
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

      {booking.issue_flag && (
        <Card className="border-2 border-[var(--color-warning)]">
          <CardBody className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-warning)]" />
            <div>
              <p className="font-semibold text-[var(--color-text-primary)]">{ISSUE_LABELS[booking.issue_flag] || "Flagged for attention"}</p>
              {booking.issue_notes && <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{booking.issue_notes}</p>}
              {booking.issue_flagged_at && (
                <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Flagged {formatDateTime(booking.issue_flagged_at)}</p>
              )}
            </div>
          </CardBody>
        </Card>
      )}

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
              <div className="rounded-xl bg-[var(--color-surface)] p-4 text-sm">
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
        {isCustomer && booking.status === "completed" && !booking.is_rated && (
          <Button variant="outline" onClick={() => setReviewOpen(true)}>
            <Star className="h-4 w-4" /> Rate this service
          </Button>
        )}
        {isCustomer && booking.status === "completed" && (
          <Button variant="outline" onClick={() => navigate("/app/book")}>
            Rebook
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
          <Input label="New date" type="date" min={todayIST()} value={newDate} onChange={(e) => setNewDate(e.target.value)} />
          <Input label="New time" type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} />
          <Button
            className="w-full"
            disabled={!newDate || !newTime}
            isLoading={rescheduleMutation.isPending}
            onClick={() => rescheduleMutation.mutate()}
          >
            Confirm reschedule
          </Button>
        </div>
      </Modal>

      <Modal open={reviewOpen} onClose={() => setReviewOpen(false)} title="Rate your experience">
        <div className="space-y-4">
          <div className="flex gap-1">
            {[1, 2, 3, 4, 5].map((r) => (
              <button key={r} onClick={() => setRating(r)}>
                <Star className={`h-7 w-7 ${r <= rating ? "fill-amber-400 text-amber-400" : "text-gray-200"}`} />
              </button>
            ))}
          </div>
          <Input label="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <Button className="w-full" isLoading={reviewMutation.isPending} onClick={() => reviewMutation.mutate()}>
            Submit review
          </Button>
        </div>
      </Modal>
    </div>
  );
}
