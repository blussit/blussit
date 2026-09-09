import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BadgeCheck, MapPin, ShieldAlert, SlidersHorizontal, Wrench } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { staffDirectoryApi } from "../../api/admin";
import { bookingPolicyApi } from "../../api/catalog";
import { getErrorMessage } from "../../lib/api-client";
import { Button, Card, EmptyState, Input, Modal, PageLoader, Select } from "../../components/ui";
import { PhotoCapture, type CapturedPhoto } from "../../components/shared/PhotoCapture";
import { NowJobCard, type JobAction } from "../../components/captain/NowJobCard";
import { JobRow } from "../../components/captain/JobRow";
import { CollectPaymentModal } from "../../components/captain/CollectPaymentModal";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { useBookingFilters } from "../../lib/useBookingFilters";
import { useLiveChannel } from "../../lib/socket";
import { useAuth } from "../../context/AuthContext";
import type { Booking, BookingStatus } from "../../types";

// How often a captain's device sends a location update while they have an
// active job — frequent enough that a manager watching (CaptainPicker /
// ManagerCaptainsPage's live map) sees real movement, sparse enough not to
// drain a phone's battery or hammer the backend. Only ever runs while
// ACTIVE_STATUSES below has at least one job — see the effect further down.
const LOCATION_PING_INTERVAL_MS = 25000;

type ModalKind = "heading" | "verify" | "before" | "after" | "cancel" | "report-risk" | null;

// "Active" (the default, unfiltered view) means "not finished yet" — a
// completed job has no more actions to take and shouldn't clutter the list
// a captain checks to see what needs doing next. Completed jobs get their
// own explicit filter instead.
const ACTIVE_STATUSES: BookingStatus[] = ["assigned", "captain_on_the_way", "service_started"];

export default function CaptainJobsPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>("");
  const [activeJob, setActiveJob] = useState<Booking | null>(null);
  const [modalKind, setModalKind] = useState<ModalKind>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [regInput, setRegInput] = useState("");
  const [riskNote, setRiskNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const [collectFor, setCollectFor] = useState<Booking | null>(null);
  const [detailJob, setDetailJob] = useState<Booking | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Per-job fee badges only make sense while the wallet system is on.
  const { data: walletPolicy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });

  const { data, isLoading } = useQuery({
    queryKey: ["my-jobs", status],
    queryFn: () => bookingApi.myJobs({ status: status || undefined, page: 1, page_size: 100 }),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["my-jobs"] });

  // Live-pushed over "user:{captainId}" — new assignment, reassignment away,
  // or a status/priority change on any of this captain's jobs. Independent
  // of whatever status tab is currently selected below.
  useLiveChannel(user ? `user:${user.id}` : null, invalidate);

  // Unfiltered (not the `status`-filtered query above) so the location
  // ping keeps running regardless of which tab the captain has open —
  // whether they currently have ANY job in an "actively working" status is
  // its own question from "what's currently displayed."
  const { data: allJobsForPing } = useQuery({
    queryKey: ["my-jobs-ping-check"],
    queryFn: () => bookingApi.myJobs({ page: 1, page_size: 100 }),
    refetchInterval: LOCATION_PING_INTERVAL_MS,
  });
  const hasActiveJob = (allJobsForPing?.data || []).some((j) => (ACTIVE_STATUSES as string[]).includes(j.status));

  useEffect(() => {
    if (!hasActiveJob || !navigator.geolocation) return;
    const send = () => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          staffDirectoryApi.pingLocation(pos.coords.latitude, pos.coords.longitude).catch(() => {
            // Best-effort background sender — a missed ping (no active job
            // anymore by the time this lands, a flaky connection, geolocation
            // denied mid-session) is never worth surfacing to the captain.
          });
        },
        () => {},
        { enableHighAccuracy: true, timeout: 15000 }
      );
    };
    send(); // one immediately, so a manager sees a fresh position right away
    const timer = setInterval(send, LOCATION_PING_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActiveJob]);
  const closeModal = () => {
    setModalKind(null);
    setActiveJob(null);
    setActionError(null);
    setCancelReason("");
    setRegInput("");
    setRiskNote("");
    beforePhotoMutation.reset();
    afterPhotoMutation.reset();
  };

  const headingMutation = useMutation({
    // equipment_used is always empty — there's no equipment-selection UI
    // yet, so the backend's inventory-deduction-on-heading-out logic is
    // currently a harmless no-op for every real captain-initiated job.
    // Deliberate, not an oversight: building that picker is a feature, not
    // a bug fix.
    mutationFn: ({ id, latitude, longitude }: { id: string; latitude: number; longitude: number }) =>
      bookingApi.startHeading(id, { latitude, longitude, equipment_used: [] }),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  const verifyMutation = useMutation({
    mutationFn: ({ id, registration_number, location }: { id: string; registration_number: string; location?: { latitude: number; longitude: number; accuracy_m?: number } }) =>
      bookingApi.verifyVehicle(id, registration_number, location),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  // "I've reached" carries GPS like every other step — blocking fix first,
  // same pattern as confirmHeading below.
  const confirmVerify = () => {
    if (!activeJob) return;
    const reg = regInput.trim();
    if (!navigator.geolocation) {
      setActionError("Geolocation isn't supported on this device.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        verifyMutation.mutate({
          id: activeJob.id,
          registration_number: reg,
          location: { latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined },
        });
      },
      (err) => {
        setLocating(false);
        setActionError(err.message || "Couldn't get your location. Enable location access and try again.");
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  };

  const beforePhotoMutation = useMutation({
    mutationFn: ({ id, photo }: { id: string; photo: CapturedPhoto }) => bookingApi.captureBeforePhoto(id, photo),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  const afterPhotoMutation = useMutation({
    mutationFn: ({ id, photo }: { id: string; photo: CapturedPhoto }) => bookingApi.captureAfterPhoto(id, photo),
    onSuccess: () => {
      invalidate();
      const job = activeJob;
      closeModal();
      // Wash done → straight into settlement (founder spec): cash tap or
      // scan-to-pay QR. Skipped entirely when the customer already paid
      // online — nothing to collect, nothing to show.
      if (job && job.payment_status !== "paid" && job.total_amount > 0) setCollectFor({ ...job, status: "completed" });
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  const cancelMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) => bookingApi.captainCancel(id, reason),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  const reportRiskMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => bookingApi.reportRisk(id, note),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

  // Lets a captain escalate a job they discover is more urgent than it
  // looked (e.g. an upset customer, a time-sensitive request) — deliberately
  // one-way (escalate to high, not a full priority editor) to keep the
  // captain's own workflow simple; a manager still has full control.
  const markUrgentMutation = useMutation({
    mutationFn: (id: string) => bookingApi.updatePriority(id, "high"),
    onSuccess: invalidate,
  });

  const openHeadingModal = (job: Booking) => {
    setActiveJob(job);
    setModalKind("heading");
    setActionError(null);
  };

  const confirmHeading = () => {
    if (!activeJob) return;
    if (!navigator.geolocation) {
      setActionError("Geolocation isn't supported on this device.");
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        headingMutation.mutate({ id: activeJob.id, latitude: pos.coords.latitude, longitude: pos.coords.longitude });
      },
      (err) => {
        setLocating(false);
        setActionError(err.message || "Couldn't get your location. Enable location access and try again.");
      },
      { enableHighAccuracy: true, timeout: 15000 }
    );
  };

  const filters = [
    { label: "Active", value: "" },
    { label: "Assigned", value: "assigned" },
    { label: "On the way", value: "captain_on_the_way" },
    { label: "In progress", value: "service_started" },
    { label: "Completed", value: "completed" },
  ];

  // A booking whose window expired long ago needs a MANAGER decision
  // (reschedule/reassign) — the backend blocks every captain action on it
  // (see start_heading's lockout), so offering the buttons here is a lie.
  const needsManager = (job: Booking) => job.issue_flag === "captain_missed_window" && !job.issue_resolved;

  const actionFor = (job: Booking): JobAction | null => {
    if (needsManager(job)) return null;
    if (job.status === "captain_on_the_way" && !job.vehicle_verified) {
      return { label: "I've reached — verify vehicle", kind: "verify" };
    }
    const map: Partial<Record<BookingStatus, JobAction>> = {
      assigned: { label: "Start heading out", kind: "heading" },
      captain_on_the_way: { label: "Capture before-photo & start", kind: "before" },
      service_started: { label: "Capture after-photo & complete", kind: "after" },
    };
    return map[job.status] ?? null;
  };

  const openActionModal = (job: Booking, kind: ModalKind) => {
    setActiveJob(job);
    setModalKind(kind);
    setActionError(null);
  };

  // Only makes sense for a job the captain hasn't started heading to yet —
  // mirrors the backend's own gate in BookingService.report_risk.
  const canReportRisk = (job: Booking) => job.status === "assigned" && !(job.issue_flag && !job.issue_resolved);

  // The default "Active" view (no explicit status filter) fetches every
  // status from the API — filter completed/cancelled back out here so it
  // only ever shows jobs still needing action.
  const statusVisible = useMemo(() => {
    const items = data?.data || [];
    return status ? items : items.filter((j) => (ACTIVE_STATUSES as string[]).includes(j.status));
  }, [data, status]);

  // Earliest-first is right for active jobs ("what needs to start soonest");
  // completed jobs read better most-recent-first ("what did I just finish")
  // — the search/sort/date controls below default accordingly but the
  // captain can flip it either way.
  const {
    filtered: visibleJobs,
    search,
    setSearch,
    sortOrder,
    setSortOrder,
    dateFrom,
    setDateFrom,
    dateTo,
    setDateTo,
  } = useBookingFilters(statusVisible, status === "completed" ? "newest" : "oldest");

  // The hero: whatever the captain is physically doing right now — an
  // in-motion/in-progress job wins, then the next job he can actually ACT
  // on (a missed-window zombie waiting on a manager must not sit on top
  // of a live job as the "NOW" card), then whatever's left.
  const nowJob = !status
    ? visibleJobs.find((j) => j.status === "captain_on_the_way" || j.status === "service_started") ||
      visibleJobs.find((j) => !needsManager(j)) ||
      visibleJobs[0] ||
      null
    : null;
  const restJobs = nowJob ? visibleJobs.filter((j) => j.id !== nowJob.id) : visibleJobs;

  const rowProps = (job: Booking) => ({
    job,
    action: actionFor(job),
    canCancel: !needsManager(job) && (job.status === "assigned" || job.status === "captain_on_the_way"),
    canReportRisk: canReportRisk(job),
    onAction: (kind: JobAction["kind"]) => (kind === "heading" ? openHeadingModal(job) : openActionModal(job, kind)),
    onCancel: () => openActionModal(job, "cancel"),
    onCollect:
      job.status === "completed" && job.payment_status === "pending" && job.total_amount > 0
        ? () => setCollectFor(job)
        : undefined,
    onReportRisk: () => openActionModal(job, "report-risk"),
    onMarkUrgent: () => markUrgentMutation.mutate(job.id),
  });

  return (
    <div className="space-y-5">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Captain</p>
        <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">Your jobs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Head out → reach & verify → before-photo → after-photo. Every step is geo-tagged.
        </p>
      </div>

      {/* One row: booking-type dropdown + the Filters toggle. The old
          horizontally-scrolling status chips cut off on phones ("In
          progres…"); a dropdown always shows the full choice. */}
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            {filters.map((f) => (
              <option key={f.label} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        </div>
        <button
          type="button"
          onClick={() => setFiltersOpen((v) => !v)}
          className={`flex shrink-0 items-center gap-1.5 rounded-xl border px-3.5 py-2.5 text-sm font-semibold transition-colors ${
            filtersOpen ? "border-[#E8A900] bg-[#FFF4CD] text-black" : "border-[#F3E5B5] bg-white text-gray-600 hover:border-[#E8A900]/50"
          }`}
        >
          <SlidersHorizontal className="h-4 w-4" /> Filters
        </button>
      </div>

      {filtersOpen && (
        <BookingFilterBar
          search={search}
          onSearchChange={setSearch}
          sortOrder={sortOrder}
          onSortOrderChange={setSortOrder}
          dateFrom={dateFrom}
          onDateFromChange={setDateFrom}
          dateTo={dateTo}
          onDateToChange={setDateTo}
          searchPlaceholder="Booking # or customer name"
        />
      )}

      {isLoading ? (
        <PageLoader />
      ) : !visibleJobs.length ? (
        <EmptyState
          icon={Wrench}
          title="No jobs found"
          description={status === "completed" ? "Jobs you've completed will show up here." : "New bookings assigned to you will appear here."}
        />
      ) : (
        <>
          {nowJob && <NowJobCard {...rowProps(nowJob)} showEarnings={!!walletPolicy?.wallet_gating_enabled} />}

          {!!restJobs.length && (
            <div>
              {nowJob && <p className="mb-2 text-xs font-bold uppercase tracking-wide text-[var(--color-text-secondary)]">{status ? "Jobs" : "Up next"}</p>}
              <Card className="overflow-hidden">
                {restJobs.map((job: Booking) => (
                  <JobRow key={job.id} {...rowProps(job)} onOpenDetails={() => setDetailJob(job)} />
                ))}
              </Card>
            </div>
          )}
        </>
      )}

      {/* Heading confirmation */}
      <Modal open={modalKind === "heading"} onClose={closeModal} title="Start heading to customer">
        <p className="text-sm text-[var(--color-text-secondary)]">
          You can only start heading out within 30 minutes of the scheduled slot. We'll capture your current location as proof.
        </p>
        {actionError && <p className="mt-3 text-sm text-[var(--color-error)]">{actionError}</p>}
        <div className="mt-5 flex gap-2">
          <Button variant="outline" className="flex-1" onClick={closeModal}>
            Cancel
          </Button>
          <Button className="flex-1" isLoading={locating || headingMutation.isPending} onClick={confirmHeading}>
            <MapPin className="h-4 w-4" /> Confirm & get location
          </Button>
        </div>
      </Modal>

      {/* Vehicle verification */}
      <Modal open={modalKind === "verify"} onClose={closeModal} title="I've reached — verify the vehicle">
        <div className="mb-3 flex items-start gap-2 rounded-lg bg-[var(--color-secondary-light)] px-3 py-2.5 text-xs text-[var(--color-text-secondary)]">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-secondary)]" />
          Type the plate you actually see on the car. If it doesn't match, do not proceed — release the job instead.
        </div>
        <Input
          label="Registration number on the vehicle"
          placeholder="MP09XX1234"
          value={regInput}
          onChange={(e) => setRegInput(e.target.value.toUpperCase())}
          autoFocus
        />
        {actionError && <p className="mt-2 text-sm text-[var(--color-error)]">{actionError}</p>}
        <Button
          className="mt-4 w-full"
          disabled={regInput.trim().length < 3}
          isLoading={locating || verifyMutation.isPending}
          onClick={confirmVerify}
        >
          <BadgeCheck className="h-4 w-4" /> I've reached — confirm vehicle
        </Button>
        <p className="mt-2 text-center text-[11px] text-[var(--color-text-secondary)]">Your location is captured with this step.</p>
      </Modal>

      {/* Before photo */}
      <Modal open={modalKind === "before"} onClose={closeModal} title="Before-service photo">
        <PhotoCapture
          label="Take a photo of the vehicle before starting"
          onCapture={(photo) => activeJob && beforePhotoMutation.mutate({ id: activeJob.id, photo })}
          submitError={beforePhotoMutation.isError ? getErrorMessage(beforePhotoMutation.error) : undefined}
        />
        {beforePhotoMutation.isPending && <p className="mt-3 text-sm text-[var(--color-text-secondary)]">Submitting…</p>}
      </Modal>

      {/* After photo */}
      <CollectPaymentModal booking={collectFor} onClose={() => setCollectFor(null)} />

      <Modal open={modalKind === "after"} onClose={closeModal} title="After-service photo">
        <PhotoCapture
          label="Take a photo of the vehicle after completing the service"
          onCapture={(photo) => activeJob && afterPhotoMutation.mutate({ id: activeJob.id, photo })}
          submitError={afterPhotoMutation.isError ? getErrorMessage(afterPhotoMutation.error) : undefined}
        />
        {afterPhotoMutation.isPending && <p className="mt-3 text-sm text-[var(--color-text-secondary)]">Submitting…</p>}
      </Modal>

      {/* Report risk */}
      <Modal open={modalKind === "report-risk"} onClose={closeModal} title="Flag this booking as at-risk">
        <div className="mb-3 flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-xs text-amber-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          Use this when your current job is running long and you're worried you won't make this booking's slot on time —
          it notifies the manager immediately so they can reassign or reschedule it.
        </div>
        <textarea
          className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
          rows={3}
          placeholder="What's going on? (optional)"
          value={riskNote}
          onChange={(e) => setRiskNote(e.target.value)}
        />
        {actionError && <p className="mt-2 text-sm text-[var(--color-error)]">{actionError}</p>}
        <div className="mt-4 flex gap-2">
          <Button variant="outline" className="flex-1" onClick={closeModal}>
            Back
          </Button>
          <Button
            className="flex-1"
            isLoading={reportRiskMutation.isPending}
            onClick={() => activeJob && reportRiskMutation.mutate({ id: activeJob.id, note: riskNote.trim() || undefined })}
          >
            <AlertTriangle className="h-4 w-4" /> Notify manager
          </Button>
        </div>
      </Modal>

      {/* Cancel */}
      <Modal open={modalKind === "cancel"} onClose={closeModal} title="Release this job">
        <p className="text-sm text-[var(--color-text-secondary)]">
          This sends the booking back to pending so a manager can reassign it. Please explain why.
        </p>
        <textarea
          className="mt-3 w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
          rows={3}
          placeholder="Reason (min 3 characters)"
          value={cancelReason}
          onChange={(e) => setCancelReason(e.target.value)}
        />
        {actionError && <p className="mt-2 text-sm text-[var(--color-error)]">{actionError}</p>}
        <div className="mt-4 flex gap-2">
          <Button variant="outline" className="flex-1" onClick={closeModal}>
            Back
          </Button>
          <Button
            variant="danger"
            className="flex-1"
            isLoading={cancelMutation.isPending}
            disabled={cancelReason.trim().length < 3}
            onClick={() => activeJob && cancelMutation.mutate({ id: activeJob.id, reason: cancelReason.trim() })}
          >
            Release job
          </Button>
        </div>
      </Modal>

      <BookingDetailDrawer booking={detailJob} onClose={() => setDetailJob(null)} />
    </div>
  );
}
