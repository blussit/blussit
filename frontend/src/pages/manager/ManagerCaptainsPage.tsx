import { useRef, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  BadgeCheck,
  Briefcase,
  CalendarCheck,
  Camera,
  ChevronRight,
  ClipboardList,
  IdCard,
  Mail,
  MapPin,
  MessageSquareWarning,
  Phone,
  Plus,
  RefreshCw,
  ShieldAlert,
  Star,
  UserRound,
  UserX,
} from "lucide-react";
import { adminUserApi, staffDirectoryApi } from "../../api/admin";
import { leaveApi } from "../../api/staffOps";
import { bookingApi } from "../../api/booking";
import { kycApi } from "../../api/staffOps";
import { reviewApi } from "../../api/engagement";
import { uploadApi } from "../../api/upload";
import { Badge, Button, Card, EmptyState, Input, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { LiveCaptainMap } from "../../components/manager/LiveCaptainMap";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatDateTime } from "../../lib/date";
import type { Booking, Review, User } from "../../types";

const ACTIVE_JOB_STATUSES = ["assigned", "captain_on_the_way", "service_started"];

const emptyForm = { full_name: "", email: "", phone: "", password: "", photo_url: "" };

const STATUS_COUNT_LABELS: Record<string, string> = {
  pending: "Pending",
  assigned: "Assigned",
  captain_on_the_way: "On the way",
  service_started: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
  rescheduled: "Rescheduled",
};

const DETAIL_TABS = [
  { key: "overview", label: "Overview", icon: UserRound },
  { key: "kyc", label: "Verification", icon: BadgeCheck },
  { key: "attendance", label: "Attendance", icon: CalendarCheck },
  { key: "jobs", label: "Jobs & reviews", icon: ClipboardList },
] as const;
type DetailTab = (typeof DETAIL_TABS)[number]["key"];

function punctualityLabel(minutes: number | null | undefined): string | null {
  if (minutes == null) return null;
  const rounded = Math.round(Math.abs(minutes));
  return minutes <= 0 ? `~${rounded} min early on average` : `~${rounded} min late on average`;
}

const KYC_CHIP: Record<string, { label: string; cls: string }> = {
  pending: { label: "Not verified", cls: "bg-gray-100 text-gray-600" },
  submitted: { label: "KYC to review", cls: "bg-amber-100 text-amber-700" },
  verified: { label: "Verified", cls: "bg-green-100 text-green-700" },
  rejected: { label: "KYC rejected", cls: "bg-red-100 text-red-700" },
};

const mapsLink = (loc?: { latitude: number; longitude: number } | null) =>
  loc ? `https://www.google.com/maps?q=${loc.latitude},${loc.longitude}` : null;

function randomTempPassword(): string {
  return `Cap${Math.random().toString(36).slice(2, 8)}${Math.floor(Math.random() * 90 + 10)}`;
}

function CaptainAvatar({ photoUrl, name, className = "h-11 w-11" }: { photoUrl?: string | null; name: string; className?: string }) {
  return photoUrl ? (
    <img src={photoUrl} alt={name} className={`${className} shrink-0 rounded-full border border-gray-200 object-cover`} />
  ) : (
    <span className={`${className} flex shrink-0 items-center justify-center rounded-full bg-[var(--color-primary-light)] text-[var(--color-primary)]`}>
      <UserRound className="h-[45%] w-[45%]" />
    </span>
  );
}

export default function ManagerCaptainsPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [photoUploading, setPhotoUploading] = useState(false);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const [detailFor, setDetailFor] = useState<User | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("overview");

  const { data, isLoading } = useQuery({
    queryKey: ["center-captains-page", centerId],
    queryFn: () => staffDirectoryApi.captainsForCenter(centerId, { page: 1, page_size: 50 }),
    enabled: !!centerId,
  });
  const captains = data?.data || [];

  // Pending leave — this approval surface simply didn't exist: captains
  // filed requests nobody could ever action.
  const { data: pendingLeave } = useQuery({
    queryKey: ["center-pending-leave", centerId],
    queryFn: () => leaveApi.pendingForCenter(centerId, { page: 1, page_size: 50 }),
    enabled: !!centerId,
  });
  const leaveReviewMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "approved" | "rejected" }) => leaveApi.review(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["center-pending-leave", centerId] }),
  });
  const captainName = (id: string) => captains.find((c) => c.id === id)?.full_name || "Captain";

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

  // Today's GPS breadcrumb trail — drawn under the live pin.
  const todayStartIso = `${new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })}T00:00:00+05:30`;
  const { data: trail } = useQuery({
    queryKey: ["captain-trail", detailFor?.id],
    queryFn: () => staffDirectoryApi.locationTrail(detailFor!.id, todayStartIso),
    enabled: !!detailFor,
  });

  // Full KYC packet (unmasked — this is the review screen, and the manager
  // is the verifier).
  const { data: kyc } = useQuery({
    queryKey: ["captain-kyc", detailFor?.id],
    queryFn: () => kycApi.forCaptain(detailFor!.id),
    enabled: !!detailFor,
  });
  const [kycNote, setKycNote] = useState("");
  const [kycError, setKycError] = useState("");
  const kycReviewMutation = useMutation({
    mutationFn: ({ status }: { status: "verified" | "rejected" }) => kycApi.review(detailFor!.id, status, kycNote.trim() || undefined),
    onSuccess: () => {
      setKycNote("");
      setKycError("");
      queryClient.invalidateQueries({ queryKey: ["captain-kyc", detailFor?.id] });
      queryClient.invalidateQueries({ queryKey: ["center-captains-page"] });
    },
    onError: (err) => setKycError(getErrorMessage(err)),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      adminUserApi.createStaff({
        full_name: form.full_name,
        email: form.email || undefined,
        phone: form.phone,
        password: form.password,
        role: "captain",
        photo_url: form.photo_url || undefined,
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

  const openDetail = (c: User) => {
    setDetailFor(c);
    setDetailTab("overview");
    setKycNote("");
    setKycError("");
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Captains</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Your service center's field team.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add captain
        </Button>
      </div>

      {!!pendingLeave?.data.length && (
        <Card className="border-amber-200 bg-amber-50/40 p-4">
          <p className="mb-3 text-sm font-bold text-amber-800">Leave requests waiting for you</p>
          <div className="space-y-2">
            {pendingLeave.data.map((l) => (
              <div key={l.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-100 bg-white p-3 text-sm">
                <div className="min-w-0">
                  <p className="font-semibold text-black">{captainName(l.captain_id)}</p>
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    {l.start_date} → {l.end_date} · {l.reason}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button size="sm" isLoading={leaveReviewMutation.isPending} onClick={() => leaveReviewMutation.mutate({ id: l.id, status: "approved" })}>
                    Approve
                  </Button>
                  <Button size="sm" variant="outline" isLoading={leaveReviewMutation.isPending} onClick={() => leaveReviewMutation.mutate({ id: l.id, status: "rejected" })}>
                    Reject
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-amber-700">Approved leave blocks assignment for those dates.</p>
        </Card>
      )}

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
                onClick={() => openDetail(c)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-3">
                    <CaptainAvatar photoUrl={c.photo_url} name={c.full_name} />
                    <div>
                      <p className="flex items-center gap-1.5 font-semibold text-[var(--color-text-primary)]">
                        {c.full_name}
                        {c.employee_id && <span className="rounded-full bg-black px-1.5 py-0.5 font-mono-num text-[9px] font-bold text-white">{c.employee_id}</span>}
                      </p>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                        <StatusBadge status={c.status} />
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${(KYC_CHIP[c.kyc_status || "pending"] || KYC_CHIP.pending).cls}`}>
                          {c.kyc_status === "verified" ? <BadgeCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                          {(KYC_CHIP[c.kyc_status || "pending"] || KYC_CHIP.pending).label}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {!c.phone && (
                  <p className="mt-2 flex items-center gap-1.5 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] font-medium text-amber-700">
                    <MessageSquareWarning className="h-3.5 w-3.5 shrink-0" /> No phone number — WhatsApp alerts can't reach this captain.
                  </p>
                )}

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
                <p className="mt-3 text-xs font-medium text-[var(--color-primary)]">Open full profile →</p>
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
          {/* Profile photo — this is what the customer sees on "Your
              captain" once he's assigned to their booking, so it's asked
              for up front, not left to KYC. */}
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => photoInputRef.current?.click()}
              className="relative flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-full border-2 border-dashed border-gray-300 bg-gray-50 text-gray-400 hover:border-gray-400"
              title="Add profile photo"
            >
              {form.photo_url ? (
                <img src={form.photo_url} alt="Captain" className="h-full w-full object-cover" />
              ) : photoUploading ? (
                <RefreshCw className="h-5 w-5 animate-spin" />
              ) : (
                <Camera className="h-6 w-6" />
              )}
            </button>
            <div>
              <p className="text-sm font-medium text-[var(--color-text-primary)]">Profile photo</p>
              <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                Shown to customers on their booking once this captain is assigned. A clear face photo builds trust at the door.
              </p>
            </div>
            <input
              ref={photoInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                setPhotoUploading(true);
                try {
                  const url = await uploadApi.photo(file);
                  setForm((f) => ({ ...f, photo_url: url }));
                } catch (err) {
                  setError(getErrorMessage(err));
                } finally {
                  setPhotoUploading(false);
                }
              }}
            />
          </div>

          <Input label="Full name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          <Input
            label="Phone number"
            value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })}
            placeholder="9876543210"
            hint="Required — job alerts and the customer's call button use this number."
            required
          />
          <Input label="Email (optional)" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <div className="flex items-end gap-2">
            <Input
              label="Temporary password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              hint="At least 8 characters — share it with the captain; they change it on first login."
              required
            />
            <Button type="button" variant="outline" className="mb-6 shrink-0" onClick={() => setForm({ ...form, password: randomTempPassword() })}>
              Generate
            </Button>
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" disabled={photoUploading || form.password.length < 8 || form.phone.trim().length < 10} isLoading={createMutation.isPending}>
            Add captain
          </Button>
        </form>
      </Modal>

      <Modal open={!!detailFor} onClose={() => setDetailFor(null)} title="Captain profile" maxWidth="max-w-3xl">
        {detailFor && (() => {
          // Re-derive from the live roster rather than the snapshot captured
          // at click time, so the status/button here update immediately
          // after a suspend/reactivate without needing to close and reopen.
          const live = captains.find((c) => c.id === detailFor.id) || detailFor;
          const hasActiveJob = captainBookings.some((b) => ACTIVE_JOB_STATUSES.includes(b.status));
          return (
            <div className="space-y-5">
              {/* Identity header — always visible, whatever tab is open. */}
              <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl bg-[var(--color-surface)] p-4">
                <div className="flex items-center gap-4">
                  <CaptainAvatar photoUrl={live.photo_url} name={live.full_name} className="h-16 w-16" />
                  <div>
                    <p className="flex flex-wrap items-center gap-2 text-lg font-bold text-[var(--color-text-primary)]">
                      {live.full_name}
                      {live.employee_id && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-black px-2 py-0.5 font-mono-num text-[10px] font-bold text-white">
                          <IdCard className="h-3 w-3" /> {live.employee_id}
                        </span>
                      )}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                      <StatusBadge status={live.status} />
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${(KYC_CHIP[live.kyc_status || "pending"] || KYC_CHIP.pending).cls}`}>
                        {live.kyc_status === "verified" ? <BadgeCheck className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                        {(KYC_CHIP[live.kyc_status || "pending"] || KYC_CHIP.pending).label}
                      </span>
                      {summary && (
                        <span className="inline-flex items-center gap-1">
                          <Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />
                          <span className="font-mono-num font-bold text-[var(--color-text-primary)]">{summary.avg_rating?.toFixed(1) ?? "—"}</span>
                          <span className="text-xs">({summary.count})</span>
                        </span>
                      )}
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-3 text-sm text-[var(--color-text-secondary)]">
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
                  </div>
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

              {/* Section tabs — one topic per screen instead of one endless
                  scroll; scrollable horizontally on small phones. */}
              <div className="-mx-1 flex gap-1 overflow-x-auto border-b border-gray-100 px-1">
                {DETAIL_TABS.map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setDetailTab(t.key)}
                    className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                      detailTab === t.key
                        ? "border-black text-[var(--color-text-primary)]"
                        : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    <t.icon className="h-4 w-4" /> {t.label}
                    {t.key === "kyc" && live.kyc_status === "submitted" && <span className="h-2 w-2 rounded-full bg-amber-500" />}
                  </button>
                ))}
              </div>

              {detailTab === "overview" && (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {[
                      { label: "Jobs completed", value: detailPerf?.total_jobs_completed ?? "—" },
                      { label: "On-time starts", value: detailPerf?.on_time_start_pct != null ? `${detailPerf.on_time_start_pct}%` : "—" },
                      { label: "Avg service time", value: detailPerf?.avg_service_minutes != null ? `${detailPerf.avg_service_minutes} min` : "—" },
                      { label: "Complaints", value: detailPerf?.repeat_complaints ?? 0 },
                    ].map((s) => (
                      <div key={s.label} className="rounded-xl border border-gray-100 p-3 text-center">
                        <p className="font-mono-num text-lg font-bold text-[var(--color-text-primary)]">{s.value}</p>
                        <p className="mt-0.5 text-[11px] text-[var(--color-text-secondary)]">{s.label}</p>
                      </div>
                    ))}
                  </div>
                  {punctualityLabel(detailPerf?.avg_heading_punctuality_minutes) && (
                    <p className="text-xs text-[var(--color-text-secondary)]">
                      Heads out {punctualityLabel(detailPerf?.avg_heading_punctuality_minutes)}.
                    </p>
                  )}
                  {hasActiveJob ? (
                    <div>
                      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Live location</p>
                      <LiveCaptainMap captainId={live.id} initialLatitude={live.latitude} initialLongitude={live.longitude} initialCapturedAt={live.last_location_at} trail={trail} />
                    </div>
                  ) : (
                    <p className="text-sm text-[var(--color-text-secondary)]">No active job right now — live tracking appears here while they're on one.</p>
                  )}
                </div>
              )}

              {detailTab === "kyc" && (
                <div>
                  {!kyc || kyc.status === "pending" ? (
                    <EmptyState icon={ShieldAlert} title="No documents yet" description="The captain submits KYC from their own app (Profile → Verification). It'll land here for your review." />
                  ) : (
                    <div className="space-y-3 rounded-xl border border-gray-100 p-3.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${(KYC_CHIP[kyc.status] || KYC_CHIP.pending).cls}`}>
                          {(KYC_CHIP[kyc.status] || KYC_CHIP.pending).label}
                        </span>
                        {kyc.submitted_at && <span className="text-xs text-[var(--color-text-secondary)]">Submitted {formatDateTime(kyc.submitted_at)}</span>}
                      </div>
                      <div className="flex flex-wrap items-start gap-4 text-sm">
                        {kyc.photo_url && <img src={kyc.photo_url} alt="Captain" className="h-20 w-20 rounded-xl object-cover" />}
                        <div className="min-w-0 space-y-1.5">
                          <p><span className="text-[var(--color-text-secondary)]">Aadhaar:</span> <span className="font-mono-num">{kyc.aadhaar_number || "—"}</span>{" "}
                            {kyc.aadhaar_doc_url && <a href={kyc.aadhaar_doc_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-[var(--color-primary)] underline">view doc</a>}
                          </p>
                          <p><span className="text-[var(--color-text-secondary)]">PAN:</span> <span className="font-mono-num">{kyc.pan_number || "—"}</span>{" "}
                            {kyc.pan_doc_url && <a href={kyc.pan_doc_url} target="_blank" rel="noreferrer" className="text-xs font-medium text-[var(--color-primary)] underline">view doc</a>}
                          </p>
                          <p><span className="text-[var(--color-text-secondary)]">Local address:</span> {kyc.local_address || "—"}</p>
                          <p><span className="text-[var(--color-text-secondary)]">Permanent:</span> {kyc.same_as_local ? "Same as local" : kyc.permanent_address || "—"}</p>
                        </div>
                      </div>
                      {kyc.review_note && <p className="text-xs text-[var(--color-text-secondary)]">Last review note: {kyc.review_note}</p>}
                      <Input placeholder="Review note (required to reject)" value={kycNote} onChange={(e) => setKycNote(e.target.value)} />
                      {kycError && <p className="text-sm text-[var(--color-error)]">{kycError}</p>}
                      <div className="flex gap-2">
                        {kyc.status !== "verified" && (
                          <Button size="sm" className="flex-1" isLoading={kycReviewMutation.isPending} onClick={() => kycReviewMutation.mutate({ status: "verified" })}>
                            <BadgeCheck className="h-3.5 w-3.5" /> Mark verified
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="outline"
                          className="flex-1"
                          disabled={!kycNote.trim()}
                          isLoading={kycReviewMutation.isPending}
                          onClick={() => kycReviewMutation.mutate({ status: "rejected" })}
                        >
                          {kyc.status === "verified" ? "Reopen (reject)" : "Reject"}
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {detailTab === "attendance" && (
                <div>
                  {attendanceLoading ? (
                    <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
                  ) : !attendance?.data.length ? (
                    <EmptyState icon={CalendarCheck} title="No check-ins recorded yet" />
                  ) : (
                    <div className="max-h-80 space-y-1.5 overflow-y-auto">
                      {attendance.data.map((a) => (
                        <div key={a.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-100 p-2.5 text-sm">
                          <span className="font-medium text-[var(--color-text-primary)]">{format(a.attendance_date)}</span>
                          <span className="flex flex-wrap items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
                            In: {a.check_in_time ? formatDateTime(a.check_in_time).split(",")[1]?.trim() : "—"}
                            {mapsLink(a.check_in_location) && (
                              <a href={mapsLink(a.check_in_location)!} target="_blank" rel="noreferrer" title="Where they checked in" className="text-[var(--color-primary)]">
                                <MapPin className="h-3 w-3" />
                              </a>
                            )}
                            {" · "}
                            Out: {a.check_out_time ? formatDateTime(a.check_out_time).split(",")[1]?.trim() : "still on duty"}
                            {mapsLink(a.check_out_location) && (
                              <a href={mapsLink(a.check_out_location)!} target="_blank" rel="noreferrer" title="Where they checked out" className="text-[var(--color-primary)]">
                                <MapPin className="h-3 w-3" />
                              </a>
                            )}
                            {a.worked_minutes != null && <span>· {Math.floor(a.worked_minutes / 60)}h {a.worked_minutes % 60}m</span>}
                          </span>
                          <Badge tone={a.status === "present" ? "success" : "neutral"}>{a.status}</Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {detailTab === "jobs" && (
                <div>
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
                    <EmptyState icon={ClipboardList} title="No bookings yet" />
                  ) : (
                    <div className="max-h-96 space-y-2 overflow-y-auto">
                      {captainBookings.map((b) => (
                        <BookingWithReview key={b.id} booking={b} review={reviewsByBookingId.get(b.id)} onOpen={() => navigate(`/manager/bookings?highlight=${b.id}`)} />
                      ))}
                    </div>
                  )}
                </div>
              )}
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
