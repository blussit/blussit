import { useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Briefcase, CalendarCheck, ChevronRight, Mail, Phone, Plus, Star, UserRound, UserX } from "lucide-react";
import { adminUserApi, staffDirectoryApi } from "../../api/admin";
import { bookingApi } from "../../api/booking";
import { reviewApi } from "../../api/engagement";
import { Badge, Button, Card, EmptyState, Input, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { LiveCaptainMap } from "../../components/manager/LiveCaptainMap";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatDateTime } from "../../lib/date";
import type { Booking, Review, User } from "../../types";

const ACTIVE_JOB_STATUSES = ["assigned", "captain_on_the_way", "service_started"];

const emptyForm = { full_name: "", email: "", phone: "", password: "" };

const STATUS_COUNT_LABELS: Record<string, string> = {
  pending: "Pending",
  assigned: "Assigned",
  captain_on_the_way: "On the way",
  service_started: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
  rescheduled: "Rescheduled",
};

function punctualityLabel(minutes: number | null | undefined): string | null {
  if (minutes == null) return null;
  const rounded = Math.round(Math.abs(minutes));
  return minutes <= 0 ? `~${rounded} min early on average` : `~${rounded} min late on average`;
}

export default function ManagerCaptainsPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [detailFor, setDetailFor] = useState<User | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["center-captains-page", centerId],
    queryFn: () => staffDirectoryApi.captainsForCenter(centerId, { page: 1, page_size: 50 }),
    enabled: !!centerId,
  });
  const captains = data?.data || [];

  // One performance card per captain — rating, jobs completed, punctuality
  // — batched via useQueries so the roster loads as one page, not one
  // request-per-captain-per-click.
  const performanceQueries = useQueries({
    queries: captains.map((c) => ({
      queryKey: ["captain-perf", c.id],
      queryFn: () => staffDirectoryApi.captainPerformance(c.id),
    })),
  });

  // Reuse the roster's already-fetched performance data instead of a second
  // request for the same captain.
  const detailPerf = performanceQueries[captains.findIndex((c) => c.id === detailFor?.id)]?.data;

  const { data: summary } = useQuery({
    queryKey: ["captain-review-summary", detailFor?.id],
    queryFn: () => reviewApi.captainSummary(detailFor!.id),
    enabled: !!detailFor,
  });

  const { data: reviews, isLoading: reviewsLoading } = useQuery({
    queryKey: ["captain-reviews", detailFor?.id],
    queryFn: () => reviewApi.captainReviews(detailFor!.id),
    enabled: !!detailFor,
  });

  // No manager-scoped "bookings for this one captain" endpoint exists —
  // reuse the same center-wide booking list the booking queue already
  // fetches, and filter to this captain client-side. Fetched only once the
  // detail modal opens, not up front for every card on the page.
  const { data: centerBookings, isLoading: bookingsLoading } = useQuery({
    queryKey: ["center-bookings-for-captain-detail", centerId],
    queryFn: () => bookingApi.forCenter(centerId, { page: 1, page_size: 100 }),
    enabled: !!detailFor && !!centerId,
  });
  const captainBookings = (centerBookings?.data || [])
    .filter((b) => b.captain_id === detailFor?.id)
    .sort((a, b) => new Date(b.scheduled_date).getTime() - new Date(a.scheduled_date).getTime());

  // Keyed by booking_id so each booking row below can show its own review
  // directly, rather than a separate disconnected reviews list — a manager
  // looking at one booking shouldn't have to cross-reference two lists to
  // see what the customer said about it.
  const reviewsByBookingId = new Map((reviews || []).map((r) => [r.booking_id, r]));

  const { data: attendance, isLoading: attendanceLoading } = useQuery({
    queryKey: ["captain-attendance", detailFor?.id],
    queryFn: () => staffDirectoryApi.attendance(detailFor!.id, { page: 1, page_size: 20 }),
    enabled: !!detailFor,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      adminUserApi.createStaff({
        full_name: form.full_name,
        email: form.email || undefined,
        phone: form.phone || undefined,
        password: form.password,
        role: "captain",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["center-captains-page"] });
      setOpen(false);
      setForm(emptyForm);
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const suspendMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.suspend(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["center-captains-page"] }),
  });

  const reactivateMutation = useMutation({
    mutationFn: (id: string) => adminUserApi.update(id, { status: "active" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["center-captains-page"] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Captains</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Your service center's field team.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add captain
        </Button>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !captains.length ? (
        <EmptyState icon={UserRound} title="No captains assigned yet" action={<Button onClick={() => setOpen(true)}>Add captain</Button>} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {captains.map((c, i) => {
            const perf = performanceQueries[i]?.data;
            const punctuality = punctualityLabel(perf?.avg_heading_punctuality_minutes);
            return (
              <Card
                key={c.id}
                className="cursor-pointer p-5 transition-shadow hover:shadow-[var(--shadow-lifted)]"
                onClick={() => setDetailFor(c)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]">
                      <UserRound className="h-5 w-5" />
                    </span>
                    <div>
                      <p className="font-semibold text-[var(--color-text-primary)]">{c.full_name}</p>
                      <StatusBadge status={c.status} />
                    </div>
                  </div>
                </div>

                <div className="mt-3 space-y-1 text-sm text-[var(--color-text-secondary)]">
                  {c.phone && (
                    <a
                      href={`tel:${c.phone}`}
                      className="flex items-center gap-1.5 hover:text-[var(--color-primary)]"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Phone className="h-3.5 w-3.5" /> {c.phone}
                    </a>
                  )}
                  {c.email && (
                    <p className="flex items-center gap-1.5">
                      <Mail className="h-3.5 w-3.5" /> {c.email}
                    </p>
                  )}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Badge tone="neutral">
                    <Star className="h-3 w-3 fill-amber-400 text-amber-400" /> {perf?.average_rating ?? "—"} ({perf?.total_reviews ?? 0})
                  </Badge>
                  <Badge tone="neutral">
                    <Briefcase className="h-3 w-3" /> {perf?.total_jobs_completed ?? 0} completed
                  </Badge>
                  {perf?.on_time_start_pct != null && (
                    <Badge tone={perf.on_time_start_pct >= 80 ? "success" : "warning"}>{perf.on_time_start_pct}% on-time start</Badge>
                  )}
                  {!!perf?.delayed_jobs && <Badge tone="warning">{perf.delayed_jobs} delayed job(s)</Badge>}
                  {!!perf?.repeat_complaints && <Badge tone="error">{perf.repeat_complaints} complaint(s)</Badge>}
                </div>
                {punctuality && <p className="mt-2 text-xs text-[var(--color-text-secondary)]">{punctuality}</p>}
                {perf?.avg_service_minutes != null && (
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    Avg service time: {perf.avg_service_minutes} min
                    {perf.jobs_per_day != null ? ` · ${perf.jobs_per_day} jobs/day` : ""}
                  </p>
                )}
                <p className="mt-3 text-xs font-medium text-[var(--color-primary)]">Click for full history →</p>
              </Card>
            );
          })}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="Add captain">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createMutation.mutate();
          }}
        >
          <Input label="Full name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          <Input label="Email (optional)" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <Input label="Phone (optional)" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <Input
            label="Temporary password"
            type="password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending}>
            Add captain
          </Button>
        </form>
      </Modal>

      <Modal open={!!detailFor} onClose={() => setDetailFor(null)} title={detailFor ? detailFor.full_name : "Captain"} maxWidth="max-w-2xl">
        {detailFor && (() => {
          // Re-derive from the live roster rather than the snapshot captured
          // at click time, so the status/button here update immediately
          // after a suspend/reactivate without needing to close and reopen.
          const live = captains.find((c) => c.id === detailFor.id) || detailFor;
          return (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-3 text-sm text-[var(--color-text-secondary)]">
                <StatusBadge status={live.status} />
                {live.phone && (
                  <a href={`tel:${live.phone}`} className="flex items-center gap-1.5 hover:text-[var(--color-primary)]">
                    <Phone className="h-3.5 w-3.5" /> {live.phone}
                  </a>
                )}
                {live.email && (
                  <span className="flex items-center gap-1.5">
                    <Mail className="h-3.5 w-3.5" /> {live.email}
                  </span>
                )}
              </div>
              {live.status !== "suspended" ? (
                <Button size="sm" variant="outline" isLoading={suspendMutation.isPending} onClick={() => suspendMutation.mutate(live.id)}>
                  <UserX className="h-3.5 w-3.5" /> Suspend
                </Button>
              ) : (
                <Button size="sm" variant="outline" isLoading={reactivateMutation.isPending} onClick={() => reactivateMutation.mutate(live.id)}>
                  Reactivate
                </Button>
              )}
            </div>

            {summary && (
              <div className="flex items-center gap-2 rounded-xl bg-[var(--color-surface)] px-4 py-3">
                <Star className="h-5 w-5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />
                <span className="font-mono-num text-lg font-bold text-[var(--color-text-primary)]">{summary.avg_rating?.toFixed(1) ?? "—"}</span>
                <span className="text-sm text-[var(--color-text-secondary)]">({summary.count} review{summary.count === 1 ? "" : "s"})</span>
              </div>
            )}

            {captainBookings.some((b) => ACTIVE_JOB_STATUSES.includes(b.status)) && (
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Live location</p>
                <LiveCaptainMap captainId={live.id} initialLatitude={live.latitude} initialLongitude={live.longitude} initialCapturedAt={live.last_location_at} />
              </div>
            )}

            <div>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
                <CalendarCheck className="h-3.5 w-3.5" /> Attendance
              </p>
              {attendanceLoading ? (
                <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
              ) : !attendance?.data.length ? (
                <p className="text-sm text-[var(--color-text-secondary)]">No check-ins recorded yet.</p>
              ) : (
                <div className="max-h-40 space-y-1.5 overflow-y-auto">
                  {attendance.data.map((a) => (
                    <div key={a.id} className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 p-2.5 text-sm">
                      <span className="font-medium text-[var(--color-text-primary)]">{format(a.attendance_date)}</span>
                      <span className="text-xs text-[var(--color-text-secondary)]">
                        In: {a.check_in_time ? formatDateTime(a.check_in_time).split(",")[1]?.trim() : "—"}
                        {" · "}
                        Out: {a.check_out_time ? formatDateTime(a.check_out_time).split(",")[1]?.trim() : "still on duty"}
                      </span>
                      <Badge tone={a.status === "present" ? "success" : "neutral"}>{a.status}</Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Bookings & reviews</p>
              {/* Counts come from the captain's own performance aggregation
                  (all-time, authoritative) rather than re-deriving them from
                  the itemized list below, which is capped to this center's
                  most recent 100 bookings. */}
              {detailPerf?.booking_status_counts && Object.keys(detailPerf.booking_status_counts).length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {Object.entries(detailPerf.booking_status_counts).map(([status, count]) => (
                    <Badge key={status} tone="neutral">
                      {STATUS_COUNT_LABELS[status] || status}: {count}
                    </Badge>
                  ))}
                </div>
              )}
              {bookingsLoading || reviewsLoading ? (
                <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
              ) : !captainBookings.length ? (
                <p className="text-sm text-[var(--color-text-secondary)]">No bookings yet.</p>
              ) : (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {captainBookings.map((b) => (
                    <BookingWithReview key={b.id} booking={b} review={reviewsByBookingId.get(b.id)} onOpen={() => navigate(`/manager/bookings?highlight=${b.id}`)} />
                  ))}
                </div>
              )}
            </div>
          </div>
          );
        })()}
      </Modal>
    </div>
  );
}

function BookingWithReview({ booking, review, onOpen }: { booking: Booking; review: Review | undefined; onOpen: () => void }) {
  const rating = review ? review.captain_rating ?? review.rating ?? 0 : null;
  const comment = review?.captain_comment ?? review?.comment;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="block w-full rounded-lg border border-gray-100 p-3 text-left text-sm transition-colors hover:border-[var(--color-primary)] hover:bg-[var(--color-primary-light)]/30"
    >
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="font-mono-num font-medium text-[var(--color-text-primary)]">{booking.booking_number}</p>
          <p className="text-xs text-[var(--color-text-secondary)]">
            {format(booking.scheduled_date)} · {booking.scheduled_slot}
            {booking.customer_name ? ` · ${booking.customer_name}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono-num text-[var(--color-text-secondary)]">₹{booking.total_amount}</span>
          <StatusBadge status={booking.status} />
          <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" />
        </div>
      </div>
      {review && rating != null && (
        <div className="mt-2 border-t border-gray-100 pt-2">
          <div className="flex items-center gap-0.5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Star key={i} className={`h-3 w-3 ${i < rating ? "fill-[var(--color-secondary)] text-[var(--color-secondary)]" : "text-gray-200"}`} />
            ))}
            <span className="ml-1.5 text-xs text-[var(--color-text-secondary)]">Customer review</span>
          </div>
          {comment && <p className="mt-1 text-xs text-[var(--color-text-primary)]">{comment}</p>}
        </div>
      )}
    </button>
  );
}
