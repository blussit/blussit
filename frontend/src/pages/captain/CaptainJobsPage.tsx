import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BadgeCheck, MapPin, ShieldAlert, Wrench } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { bookingPolicyApi } from "../../api/catalog";
import { getErrorMessage } from "../../lib/api-client";
import { Button, EmptyState, Input, Modal, PageLoader } from "../../components/ui";
import { PhotoCapture, type CapturedPhoto } from "../../components/shared/PhotoCapture";
import { JobCard, type JobAction } from "../../components/captain/JobCard";
import { BookingFilterBar } from "../../components/shared/BookingFilterBar";
import { useBookingFilters } from "../../lib/useBookingFilters";
import type { Booking, BookingStatus } from "../../types";

type ModalKind = "heading" | "verify" | "before" | "after" | "cancel" | "report-risk" | null;

// "Active" (the default, unfiltered view) means "not finished yet" — a
// completed job has no more actions to take and shouldn't clutter the list
// a captain checks to see what needs doing next. Completed jobs get their
// own explicit filter instead.
const ACTIVE_STATUSES: BookingStatus[] = ["assigned", "captain_on_the_way", "service_started"];

export default function CaptainJobsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string>("");
  const [activeJob, setActiveJob] = useState<Booking | null>(null);
  const [modalKind, setModalKind] = useState<ModalKind>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [regInput, setRegInput] = useState("");
  const [riskNote, setRiskNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["my-jobs", status],
    queryFn: () => bookingApi.myJobs({ status: status || undefined, page: 1, page_size: 100 }),
  });
  const { data: policy } = useQuery({ queryKey: ["booking-policy"], queryFn: bookingPolicyApi.get });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
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
    mutationFn: ({ id, registration_number }: { id: string; registration_number: string }) => bookingApi.verifyVehicle(id, registration_number),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (e) => setActionError(getErrorMessage(e)),
  });

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
      closeModal();
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

  const actionFor = (job: Booking): JobAction | null => {
    if (job.status === "captain_on_the_way" && !job.vehicle_verified) {
      return { label: "Verify vehicle registration", kind: "verify" };
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Today's jobs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Heading out, vehicle verification, before-photo, and after-photo are all required in order — geo-tagged every step.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {filters.map((f) => (
          <button
            key={f.label}
            onClick={() => setStatus(f.value)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
              status === f.value ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

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

      {isLoading ? (
        <PageLoader />
      ) : !visibleJobs.length ? (
        <EmptyState
          icon={Wrench}
          title="No jobs found"
          description={status === "completed" ? "Jobs you've completed will show up here." : "New bookings assigned to you will appear here."}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {visibleJobs.map((job: Booking) => (
            <JobCard
              key={job.id}
              job={job}
              action={actionFor(job)}
              canCancel={job.status === "assigned" || job.status === "captain_on_the_way"}
              canReportRisk={canReportRisk(job)}
              showEarnings={!!policy?.wallet_gating_enabled}
              onAction={(kind) => (kind === "heading" ? openHeadingModal(job) : openActionModal(job, kind))}
              onCancel={() => openActionModal(job, "cancel")}
              onReportRisk={() => openActionModal(job, "report-risk")}
            />
          ))}
        </div>
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
      <Modal open={modalKind === "verify"} onClose={closeModal} title="Verify vehicle on arrival">
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
          isLoading={verifyMutation.isPending}
          onClick={() => activeJob && verifyMutation.mutate({ id: activeJob.id, registration_number: regInput.trim() })}
        >
          <BadgeCheck className="h-4 w-4" /> Confirm this is the right vehicle
        </Button>
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
    </div>
  );
}
