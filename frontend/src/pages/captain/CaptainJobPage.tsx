/**
 * Captain · one job, step by step (the design's screens 2–6), driven by the
 * booking's real state — see components/captain/jobState.ts:
 *
 *   Booking details  assigned            → Start trip   (POST /heading, GPS)
 *                    on the way          → I've arrived (POST /verify-vehicle: code + GPS geofence)
 *   Before photo     arrived             → Start wash   (POST /before-photo → service_started)
 *   Job in progress  service_started     → Wash done    (opens the after photo)
 *   After photo                          → Complete     (POST /after-photo → completed, wallet settles)
 *   Completed        completed           → collect if unpaid (cash / QR; never cash on prepaid)
 *
 * A multi-car visit is one trip: Start trip and the code cover every car;
 * photos stay per car (the car chips pick which), payment once at the end.
 * Rare actions (running late, release job) live in the ⋯ sheet.
 */
import { type ReactNode, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import {
  CarFront,
  Check,
  Clock,
  Ellipsis,
  FileText,
  IndianRupee,
  Lock,
  MapPin,
  MessageCircle,
  Navigation,
  Phone,
  PlusCircle,
  Sparkles,
  TriangleAlert,
  Undo2,
} from "lucide-react";
import { bookingApi } from "../../api/booking";
import { getErrorCode, getErrorMessage, getErrorStatus, retryUnlessClientError } from "../../lib/api-client";
import { translateCaptainError } from "../../lib/captainErrorTranslations";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { useActiveJobs } from "../../components/captain/CaptainShell";
import { CollectPayment } from "../../components/captain/CollectPayment";
import { AddServicesSheet } from "../../components/captain/AddServicesSheet";
import { CustomerEditedNote, JobMoney } from "../../components/captain/JobMoney";
import { PhotoTile, usePhotoShot } from "../../components/captain/PhotoShot";
import { getPosition } from "../../components/captain/photo";
import {
  addressLine,
  blockedByFlag,
  carFull,
  carIdentity,
  carService,
  carTitle,
  clock,
  currentCar,
  headOutOpensAt,
  isFinished,
  mapsUrl,
  needsManager,
  paymentOf,
  pickNext,
  rupees,
  serviceName,
  slabKeyOf,
  stepOf,
  telUrl,
  useCarTypes,
  visitCarService,
  waUrl,
  whenLabel,
} from "../../components/captain/jobState";
import { BottomBar, Btn, InfoRow, LoadError, Notice, Panel, Sheet, TopBar } from "../../components/captain/ui";
import { cn } from "../../lib/cn";
import type { Booking } from "../../types";

type SheetKind = null | "arrive" | "more" | "late" | "release" | "add";

export default function CaptainJobPage() {
  const { id = "" } = useParams();
  // A fresh screen per job — "Next booking" must not inherit this one's state.
  return <JobScreen key={id} id={id} />;
}

function JobScreen({ id }: { id: string }) {
  const { t, language } = useCaptainTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const active = useActiveJobs();

  const visit = useQuery({
    queryKey: ["captain-visit", id],
    queryFn: async () => {
      const booking = await bookingApi.get(id);
      if (!booking.booking_group_id) return [booking];
      const all = await bookingApi.getGroup(booking.booking_group_id);
      const mine = all.filter((c) => c.captain_id === booking.captain_id && c.status !== "cancelled");
      return mine.length ? mine : [booking];
    },
    // A reassigned/removed job answers 4xx — that's final ("job gone"); a
    // dropped connection gets two more tries, then an error with a retry.
    retry: retryUnlessClientError(2),
  });

  const [pickedId, setPickedId] = useState<string | null>(null);
  const [afterMode, setAfterMode] = useState(false);
  const [summary, setSummary] = useState(false);
  const [sheet, setSheet] = useState<SheetKind>(() => ((location.state as { open?: string } | null)?.open === "arrive" ? "arrive" : null));
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  // "Added — collect ₹X at the end." after an on-site add.
  const [addedMsg, setAddedMsg] = useState("");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  const geoFail = t("captain.error.geoFailed");
  const beforeShot = usePhotoShot(geoFail);
  const afterShot = usePhotoShot(geoFail);

  const cars = visit.data ?? [];
  const types = useCarTypes(cars);
  const openCars = cars.filter((c) => !isFinished(c));
  const routeCar = cars.find((c) => c.id === id);
  const car: Booking | undefined =
    cars.find((c) => c.id === pickedId) || (routeCar && !isFinished(routeCar) ? routeCar : openCars[0]) || routeCar || cars[0];

  // Switching cars on a visit starts that car's photos from scratch.
  const carId = car?.id;
  useEffect(() => {
    beforeShot.input.reset();
    afterShot.input.reset();
    setAfterMode(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [carId]);

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["my-jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["captain-visit", id] }),
      // A finished/collected job moves his earnings.
      queryClient.invalidateQueries({ queryKey: ["my-wallet"] }),
    ]);
  // A photo step that failed because storage is down keeps the photo and
  // offers the same step again.
  const [retryStep, setRetryStep] = useState<"before" | "after" | null>(null);
  const fail = (e: unknown) =>
    setError(
      axios.isAxiosError(e) ? translateCaptainError(getErrorMessage(e), language) : e instanceof Error && e.message ? e.message : getErrorMessage(e),
    );
  /** Photo steps: storage down → keep the photo, offer a retry; the server
   *  refusing THIS photo (not uploaded from the app, already used, too old,
   *  not a new photo) → its own words, and the photo is cleared to retake. */
  const failPhoto = (e: unknown, shot: typeof beforeShot, which: "before" | "after") => {
    if (getErrorCode(e) === "STORAGE_UNAVAILABLE") {
      setRetryStep(which);
      setError(t("captain.v2.storageDown"));
      return;
    }
    fail(e);
    const message = axios.isAxiosError(e) ? getErrorMessage(e) : "";
    if (getErrorStatus(e) === 400 && /photo/i.test(message) && /(take|again|new photo|fresh photo|already used|too old)/i.test(message)) shot.input.reset();
  };

  const heading = useMutation({
    mutationFn: async () => {
      const at = await getPosition(geoFail);
      return bookingApi.startHeading(car!.id, { latitude: at.latitude, longitude: at.longitude, equipment_used: [] });
    },
    onMutate: () => setError(""),
    onSuccess: refresh,
    onError: fail,
  });
  const byCode = car ? (car.requires_service_code ?? !!car.service_code) : true;
  const verify = useMutation({
    // The value is passed in when the 4th digit auto-submits (state hasn't
    // caught up yet); the button sends what's in the field.
    mutationFn: async (typed?: string) => {
      const value = typed ?? code;
      const at = await getPosition(geoFail);
      return bookingApi.verifyVehicle(car!.id, byCode ? { service_code: value } : { registration_number: value.trim() }, at);
    },
    onMutate: () => setError(""),
    onSuccess: async () => {
      setSheet(null);
      setCode("");
      await refresh();
    },
    onError: fail,
  });
  const before = useMutation({
    mutationFn: async () => bookingApi.captureBeforePhoto(car!.id, await beforeShot.submit()),
    onMutate: () => {
      setError("");
      setRetryStep(null);
      setPickedId(car!.id);
    },
    onSuccess: refresh,
    onError: (e) => failPhoto(e, beforeShot, "before"),
  });
  const after = useMutation({
    mutationFn: async () => bookingApi.captureAfterPhoto(car!.id, await afterShot.submit()),
    onMutate: () => {
      setError("");
      setRetryStep(null);
      setPickedId(car!.id);
    },
    onSuccess: async () => {
      await refresh();
      setAfterMode(false);
    },
    onError: (e) => failPhoto(e, afterShot, "after"),
  });
  const risk = useMutation({
    mutationFn: () => bookingApi.reportRisk(car!.id, note.trim() || undefined),
    onMutate: () => setError(""),
    onSuccess: async () => {
      setSheet(null);
      setNote("");
      await refresh();
    },
    onError: fail,
  });
  const release = useMutation({
    mutationFn: () => bookingApi.captainCancel(car!.id, note.trim()),
    onMutate: () => setError(""),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["my-jobs"] });
      navigate("/captain", { replace: true });
    },
    onError: fail,
  });

  const goBack = () => (location.key !== "default" ? navigate(-1) : navigate("/captain"));

  if (visit.isLoading) {
    return (
      <>
        <TopBar title={t("captain.v2.title.details")} back={goBack} />
        <div className="space-y-3">
          <Panel className="h-20 animate-pulse bg-[#EEF3FA]"><span /></Panel>
          <Panel className="h-72 animate-pulse bg-[#EEF3FA]"><span /></Panel>
        </div>
      </>
    );
  }
  const status = visit.isError ? getErrorStatus(visit.error) : undefined;
  if (!car && visit.isError && !(status !== undefined && status >= 400 && status < 500)) {
    return (
      <>
        <TopBar title={t("captain.v2.title.details")} back={goBack} />
        <LoadError
          title={t("captain.v2.jobLoadFailed")}
          sub={t("captain.v2.loadFailedSub")}
          retryLabel={t("captain.v2.tryAgain")}
          busy={visit.isFetching}
          onRetry={() => void visit.refetch()}
        />
      </>
    );
  }
  if (!car) {
    return (
      <>
        <TopBar title={t("captain.v2.title.details")} back="/captain" />
        <Panel className="px-4 py-10 text-center">
          <p className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.jobGone")}</p>
          <Btn className="mt-4 w-full" onClick={() => navigate("/captain", { replace: true })}>{t("captain.v2.tab.today")}</Btn>
        </Panel>
      </>
    );
  }

  const step = stepOf(car);
  const isVisit = cars.length > 1;
  const pay = paymentOf(cars);
  // A previous late-cancellation charge riding on this visit (in the total to collect).
  const carriedCharge = cars.filter((c) => c.status !== "cancelled").reduce((sum, c) => sum + (Number(c.cancellation_charge) || 0), 0);
  const stuck = needsManager(car);
  const flagged = blockedByFlag(car);
  const reached = cars.some((c) => c.vehicle_verified || c.service_started_at || c.status === "completed");
  const url = mapsUrl(car);
  const canNavigate = car.status === "captain_on_the_way" && !reached;
  const opensAt = step === "start" ? headOutOpensAt(cars) : null;
  const tooEarly = opensAt != null && opensAt > now;
  const canRisk = step === "start" && !(car.issue_flag && !car.issue_resolved);
  const canRelease = (step === "start" || step === "arrive") && !stuck;
  const allDone = openCars.length === 0;
  const lastOpen = openCars.length === 1 && openCars[0].id === car.id;
  const words = { today: t("captain.v2.today"), tomorrow: t("captain.v2.tomorrow") };
  const showDetails = summary || step === "start" || step === "arrive" || step === "closed";
  // On-site add-ons (spec 1.4): after arrival is verified, during the wash,
  // and after completion while the visit still owes money.
  const canAdd =
    !stuck &&
    ((car.status === "captain_on_the_way" && !!car.vehicle_verified) || car.status === "service_started" || (car.status === "completed" && pay.due > 0.004));
  const addButton = canAdd ? (
    <Btn
      variant="outline"
      className="w-full"
      onClick={() => {
        setError("");
        setAddedMsg("");
        setSheet("add");
      }}
      data-testid="captain-add-service"
    >
      <PlusCircle className="h-5 w-5 text-[#0A66F0]" /> {t("captain.onsite.addService")}
    </Btn>
  ) : null;
  const addedNotice = addedMsg ? (
    <Notice tone="green" icon={<Check className="h-4 w-4" />}>
      {addedMsg}
    </Notice>
  ) : null;

  const title = summary
    ? t("captain.v2.title.details")
    : {
        start: t("captain.v2.title.details"),
        arrive: t("captain.v2.title.details"),
        before: t("captain.v2.title.before"),
        after: afterMode ? t("captain.v2.title.after") : t("captain.v2.title.progress"),
        done: t("captain.v2.title.done"),
        closed: t("captain.v2.title.details"),
      }[step];

  const goNext = () => {
    const next = pickNext(active.data?.data ?? [], slabKeyOf(car));
    navigate(next ? `/captain/jobs/${currentCar(next).id}` : "/captain", { replace: true });
  };

  const payText = pay.plan
    ? t("captain.v2.pay.planShort")
    : pay.paid
      ? cars.some((c) => c.payment_method === "cash")
        ? t("captain.v2.pay.paidCash")
        : t("captain.v2.pay.paidShort")
      : pay.paidAmount > 0 || pay.wallet > 0
        ? t("captain.v2.pay.partShort").replace("{amount}", rupees(pay.due))
        : pay.prepaid
          ? t("captain.v2.pay.prepaidShort")
          : pay.cash
            ? t("captain.v2.pay.cashShort").replace("{amount}", rupees(pay.due))
            : t("captain.v2.pay.onlineShort");

  // Car type first, then the service — "Sedan · Star Wash" — with the
  // make/plate under it when the booking has them.
  const strip = (main: ReactNode, sub?: string) => (
    <div className="mb-3 flex items-center gap-3 rounded-[16px] bg-[#EEF3FA] px-3.5 py-3">
      <CarFront className="h-5 w-5 shrink-0 text-[#0A66F0]" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-bold text-[#0E1A33]">{main}</p>
        {sub && <p className="truncate tabular-nums text-[13px] font-medium text-[#5F6878]">{sub}</p>}
      </div>
    </div>
  );
  const contextStrip = strip(
    <>
      {carTitle(car, types)} · <span className="font-semibold text-[#5F6878]">{serviceName(car)}</span>
    </>,
    carIdentity(car, types),
  );

  return (
    <>
      <TopBar
        title={title}
        back={summary ? () => setSummary(false) : afterMode ? () => setAfterMode(false) : goBack}
        right={
          (canRisk || canRelease) && !summary ? (
            <button
              type="button"
              aria-label={t("captain.v2.more")}
              onClick={() => {
                setError("");
                setSheet("more");
              }}
              className="flex h-11 w-11 items-center justify-center rounded-full text-[#0E1A33] active:bg-[#EEF3FA]"
            >
              <Ellipsis className="h-6 w-6" />
            </button>
          ) : null
        }
      />

      {isVisit && (
        <div className="mb-3 flex gap-2 overflow-x-auto pb-1" role="tablist">
          {cars.map((c, i) => {
            const on = c.id === car.id;
            return (
              <button
                key={c.id}
                type="button"
                role="tab"
                aria-selected={on}
                onClick={() => {
                  setPickedId(c.id);
                  setSummary(false);
                  setError("");
                }}
                className={cn(
                  "flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-sm font-bold",
                  on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33]",
                )}
              >
                {c.status === "completed" && <Check className="h-4 w-4 text-[#15803D]" />}
                {t("captain.visit.car")} {i + 1} · {carTitle(c, types)}
              </button>
            );
          })}
        </div>
      )}

      {!summary && step !== "closed" && (
        <div className="mb-3">
          <CustomerEditedNote cars={cars} />
        </div>
      )}

      {showDetails && (
        <div className="space-y-3">
          {isVisit ? strip(visitCarService(cars, types)) : contextStrip}
          <Panel className="flex items-center gap-3 p-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#E8F0FE] text-lg font-extrabold text-[#0A66F0]">
              {(car.customer_name || "C").trim().charAt(0).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-[#5F6878]">{t("captain.job.customer")}</p>
              <p className="truncate text-[16px] font-bold text-[#0E1A33]">{car.customer_name || t("captain.job.customer")}</p>
              {car.customer_phone && <p className="tabular-nums text-sm text-[#5F6878]">{car.customer_phone}</p>}
            </div>
            {telUrl(car.customer_phone) && (
              <a href={telUrl(car.customer_phone)!} aria-label={t("captain.v2.call")} className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#E8F0FE] text-[#0A66F0]">
                <Phone className="h-5 w-5" />
              </a>
            )}
            {waUrl(car.customer_phone) && (
              <a href={waUrl(car.customer_phone)!} target="_blank" rel="noreferrer" aria-label="WhatsApp" className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[#E7F6EC] text-[#15803D]">
                <MessageCircle className="h-5 w-5" />
              </a>
            )}
          </Panel>
          {car.alternate_contact_phone && (
            <a href={telUrl(car.alternate_contact_phone) || undefined} className="flex min-h-[44px] items-center gap-2 px-1 text-sm font-semibold text-[#0A66F0]">
              <Phone className="h-4 w-4" /> {t("captain.v2.altContact")}: {car.alternate_contact_name ? `${car.alternate_contact_name} · ` : ""}
              {car.alternate_contact_phone}
            </a>
          )}

          <Panel className="divide-y divide-[#E4E9F1]">
            <InfoRow icon={<CarFront className="h-5 w-5" />} label={t("captain.job.vehicle")}>
              {isVisit ? cars.map((c, i) => <span key={c.id} className="block">{i + 1}. {carFull(c, types)}</span>) : carFull(car, types)}
            </InfoRow>
            <InfoRow
              icon={<Sparkles className="h-5 w-5" />}
              label={t("captain.v2.service")}
              right={<span className="tabular-nums text-[17px] font-extrabold text-[#0E1A33]">{rupees(isVisit ? pay.total : car.total_amount)}</span>}
            >
              {isVisit ? [...new Set(cars.map(serviceName))].join(" + ") : serviceName(car)}
            </InfoRow>
            <InfoRow icon={<IndianRupee className="h-5 w-5" />} label={t("captain.v2.payment")}>
              {payText}
              {carriedCharge > 0 && (
                <span className="block text-sm font-medium text-[#5F6878]" data-testid="captain-carried-charge">
                  {t("captain.v2.pay.includesCharge").replace("{amount}", rupees(carriedCharge))}
                </span>
              )}
            </InfoRow>
            <InfoRow icon={<MapPin className="h-5 w-5" />} label={t("captain.job.location")}>
              {addressLine(car) || t("captain.job.addressUnavailable")}
              <span className="block text-sm font-medium text-[#5F6878]">
                {[
                  car.address_snapshot?.city,
                  car.travel_distance_km != null ? t("captain.v2.kmAway").replace("{km}", String(car.travel_distance_km)) : "",
                  car.travel_eta_minutes != null ? `~${car.travel_eta_minutes} ${t("captain.job.minRide")}` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </InfoRow>
            <InfoRow icon={<Clock className="h-5 w-5" />} label={t("captain.v2.time")}>
              {whenLabel(car, words, true)}
            </InfoRow>
            {car.customer_notes && (
              <InfoRow icon={<FileText className="h-5 w-5" />} label={t("captain.v2.note")}>
                <span className="font-medium">{car.customer_notes}</span>
              </InfoRow>
            )}
          </Panel>

          {step !== "closed" && <JobMoney cars={cars} types={types} />}

          {summary && (car.before_photo || car.after_photo) && (
            <div className="grid grid-cols-2 gap-2">
              {[
                { label: t("captain.v2.title.before"), photo: car.before_photo },
                { label: t("captain.v2.title.after"), photo: car.after_photo },
              ].map((p) =>
                p.photo ? (
                  <a key={p.label} href={p.photo.image_url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-[16px] border border-[#E4E9F1]">
                    <img src={p.photo.image_url} alt={p.label} className="aspect-[4/3] w-full object-cover" />
                    <p className="px-3 py-2 text-xs font-bold text-[#5F6878]">{p.label}</p>
                  </a>
                ) : null,
              )}
            </div>
          )}

          {!summary && stuck && <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.jobs.waiting.desc")}</Notice>}
          {!summary && !stuck && flagged && <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.v2.flagged")}</Notice>}
          {!summary && step === "start" && tooEarly && !stuck && (
            <Notice tone="amber" icon={<Clock className="h-4 w-4" />}>
              {t("captain.v2.opensAt").replace("{time}", clock(new Date(opensAt!).toISOString()))}
            </Notice>
          )}
          {!summary && step === "arrive" && !flagged && <Notice tone="blue">{t("captain.v2.arriveHint")}</Notice>}
          {!summary && step === "closed" && <Notice tone="gray">{t("captain.v2.closed")}</Notice>}
        </div>
      )}

      {!summary && step === "before" && (
        <div className="space-y-3">
          {contextStrip}
          {isVisit && cars.filter((c) => c.vehicle_verified && c.status === "captain_on_the_way").length > 1 && (
            <Notice tone="blue">{t("captain.job.pickCarHint")}</Notice>
          )}
          <p className="text-[15px] font-semibold text-[#0E1A33]">{t("captain.v2.beforeHint")}</p>
          <PhotoTile
            shot={beforeShot}
            label={t("captain.v2.photoAngle")}
            tapText={t("captain.v2.tapPhoto")}
            locatingText={t("captain.v2.locating")}
            retakeText={t("captain.v2.retake")}
            gpsText={t("captain.v2.gpsTagged")}
          />
          {flagged && <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.v2.flagged")}</Notice>}
          {addedNotice}
          <JobMoney cars={cars} types={types} />
          {addButton}
        </div>
      )}

      {!summary && step === "after" && !afterMode && (
        <div className="space-y-3">
          {contextStrip}
          <Panel className="p-4">
            <WashStepper car={car} now={now} />
          </Panel>
          <JobMoney cars={cars} types={types} />
          {addedNotice}
          {addButton}
        </div>
      )}

      {!summary && step === "after" && afterMode && (
        <div className="space-y-3">
          {contextStrip}
          <p className="text-[15px] font-semibold text-[#0E1A33]">{t("captain.v2.afterHint")}</p>
          <PhotoTile
            shot={afterShot}
            label={t("captain.v2.photoAngle")}
            tapText={t("captain.v2.tapPhoto")}
            locatingText={t("captain.v2.locating")}
            retakeText={t("captain.v2.retake")}
            gpsText={t("captain.v2.gpsTagged")}
          />
          {lastOpen && pay.due > 0.004 && (
            pay.prepaid ? (
              <Notice tone="amber" icon={<IndianRupee className="h-4 w-4" />}>{t("captain.v2.pay.prepaidNoCash")}</Notice>
            ) : (
              <Notice tone="blue" icon={<IndianRupee className="h-4 w-4" />}>{t("captain.v2.pay.cashBefore").replace("{amount}", rupees(pay.due))}</Notice>
            )
          )}
          {flagged && <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.v2.flagged")}</Notice>}
        </div>
      )}

      {!summary && step === "done" && (
        <div className="space-y-4">
          <div className="pt-4 text-center">
            <span className="mx-auto flex h-20 w-20 items-center justify-center rounded-[22px] bg-[#16A34A] text-white">
              <Check className="h-10 w-10" strokeWidth={3} />
            </span>
            <p className="mt-4 text-[22px] font-extrabold text-[#0E1A33]">
              {isVisit && !allDone ? t("captain.v2.carDone").replace("{n}", String(cars.findIndex((c) => c.id === car.id) + 1)) : t("captain.v2.doneTitle")}
            </p>
            <p className="mt-1 px-2 text-[15px] font-semibold text-[#5F6878]">
              {isVisit && allDone ? visitCarService(cars, types) : carService(car, types)}
            </p>
            <p className="mt-1 tabular-nums text-[34px] font-extrabold text-[#0E1A33]">{rupees(isVisit && allDone ? pay.total : car.total_amount)}</p>
            {car.completed_at && <p className="text-sm font-medium text-[#5F6878]">{t("captain.v2.completedAt").replace("{time}", clock(car.completed_at))}</p>}
          </div>
          <JobMoney cars={cars} types={types} />
          {addedNotice}
          {isVisit && !allDone ? (
            <Notice tone="blue">{t("captain.v2.moreCars").replace("{n}", String(openCars.length))}</Notice>
          ) : (
            <CollectPayment cars={cars} anchor={car} />
          )}
          {addButton}
          {/* The done bar holds two buttons — keep the last card clear of it. */}
          <div className="h-12" aria-hidden />
        </div>
      )}

      {/* ------------------------------------------------------------ actions */}
      {!summary && (
        <BottomBar>
          {error && (
            <p className="mb-2 text-sm font-medium text-[#B91C1C]" role="alert">
              {error}
              {retryStep && (
                <button
                  type="button"
                  className="ml-2 font-bold text-[#0A66F0] underline-offset-2 hover:underline disabled:opacity-60"
                  disabled={before.isPending || after.isPending}
                  onClick={() => (retryStep === "before" ? before.mutate() : after.mutate())}
                >
                  {t("captain.v2.tryAgain")}
                </button>
              )}
            </p>
          )}
          {(step === "start" || step === "arrive") && !stuck && (
            <div className="grid grid-cols-2 gap-2">
              {canNavigate && url ? (
                <a href={url} target="_blank" rel="noreferrer" className="inline-flex min-h-[52px] items-center justify-center gap-2 rounded-2xl bg-[#E8F0FE] text-[15px] font-bold text-[#0A66F0]">
                  <Navigation className="h-5 w-5" /> {t("captain.job.navigate")}
                </a>
              ) : (
                <Btn variant="secondary" className="opacity-80" onClick={() => setError(t("captain.job.navigateLockedTip"))}>
                  <Lock className="h-4 w-4" /> {t("captain.job.navigate")}
                </Btn>
              )}
              {step === "start" ? (
                <Btn loading={heading.isPending} disabled={flagged || tooEarly} onClick={() => heading.mutate()}>
                  {heading.isPending ? t("captain.v2.locating") : t("captain.v2.act.startTrip")}
                </Btn>
              ) : (
                <Btn
                  disabled={flagged}
                  onClick={() => {
                    setError("");
                    setSheet("arrive");
                  }}
                >
                  {t("captain.v2.act.arrived")}
                </Btn>
              )}
            </div>
          )}
          {step === "before" && (
            <Btn className="w-full" disabled={!beforeShot.ready || flagged} loading={before.isPending} onClick={() => before.mutate()}>
              {t("captain.v2.act.startWash")}
            </Btn>
          )}
          {step === "after" && !afterMode && (
            <Btn className="w-full" disabled={flagged} onClick={() => setAfterMode(true)}>
              {t("captain.v2.act.washDone")}
            </Btn>
          )}
          {step === "after" && afterMode && (
            <Btn className="w-full" disabled={!afterShot.ready || flagged} loading={after.isPending} onClick={() => after.mutate()}>
              {t("captain.v2.act.complete")}
            </Btn>
          )}
          {step === "done" && (
            <div className="grid gap-2">
              {isVisit && !allDone ? (
                <Btn onClick={() => setPickedId(openCars[0].id)}>{t("captain.v2.act.nextCar")}</Btn>
              ) : (
                // Money still to collect: that panel is the main action, not "next".
                <Btn variant={allDone && pay.due > 0.004 ? "outline" : "primary"} onClick={goNext}>
                  {t("captain.v2.act.nextBooking")}
                </Btn>
              )}
              <Btn variant="outline" onClick={() => setSummary(true)}>
                {t("captain.actions.viewDetails")}
              </Btn>
            </div>
          )}
          {(stuck || step === "closed") && (
            <Btn variant="outline" className="w-full" onClick={() => navigate("/captain")}>
              {t("captain.v2.tab.today")}
            </Btn>
          )}
        </BottomBar>
      )}

      {/* ------------------------------------------------------------ sheets */}
      <Sheet open={sheet === "arrive" && step === "arrive"} onClose={() => setSheet(null)} title={byCode ? t("captain.v2.codeTitle") : t("captain.modal.verifyTitle")}>
        <p className="text-sm text-[#5F6878]">{byCode ? t("captain.v2.codeHint") : t("captain.modal.plateHint")}</p>
        {byCode ? (
          <input
            aria-label={t("captain.modal.codeLabel")}
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            maxLength={4}
            value={code}
            onChange={(e) => {
              const next = e.target.value.replace(/\D/g, "").slice(0, 4);
              setCode(next);
              // The 4th digit submits by itself — on a phone the keyboard
              // otherwise covers the button.
              if (next.length === 4 && next !== code && !verify.isPending) verify.mutate(next);
            }}
            placeholder="••••"
            className="mt-4 h-16 w-full rounded-2xl border border-[#E4E9F1] text-center tabular-nums text-[32px] font-extrabold tracking-[0.5em] text-[#0E1A33] outline-none focus:border-[#0A66F0] focus:ring-2 focus:ring-[#E8F0FE]"
          />
        ) : (
          <input
            aria-label={t("captain.modal.plateLabel")}
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="MP09XX1234"
            className="mt-4 h-14 w-full rounded-2xl border border-[#E4E9F1] px-4 tabular-nums text-xl font-bold text-[#0E1A33] outline-none focus:border-[#0A66F0] focus:ring-2 focus:ring-[#E8F0FE]"
          />
        )}
        {error && <p className="mt-2 text-sm font-medium text-[#B91C1C]" role="alert">{error}</p>}
        <Btn
          className="mt-4 w-full"
          disabled={byCode ? code.length !== 4 : code.trim().length < 3}
          loading={verify.isPending}
          onClick={() => verify.mutate(undefined)}
        >
          {verify.isPending ? t("captain.v2.locating") : t("captain.v2.verifyCta")}
        </Btn>
        <p className="mt-2 text-center text-xs font-medium text-[#5F6878]">{t("captain.v2.locNote")}</p>
      </Sheet>

      {canAdd && (
        <AddServicesSheet
          key={car.id}
          open={sheet === "add"}
          onClose={() => setSheet(null)}
          car={car}
          cars={cars}
          types={types}
          onAdded={async (res) => {
            setSheet(null);
            setAddedMsg(t("captain.onsite.addedDone").replace("{amount}", rupees(res.visit_amount_due)));
            await Promise.all([refresh(), queryClient.invalidateQueries({ queryKey: ["collect-status"] })]);
          }}
        />
      )}

      <Sheet open={sheet === "more"} onClose={() => setSheet(null)} title={t("captain.v2.more")}>
        <div className="space-y-2">
          {canRisk && (
            <button type="button" onClick={() => setSheet("late")} className="flex min-h-[56px] w-full items-center gap-3 rounded-[16px] border border-[#E4E9F1] px-4 text-left active:bg-[#EEF3FA]">
              <Clock className="h-5 w-5 text-[#9A6400]" />
              <span className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.runningLate")}</span>
            </button>
          )}
          {canRelease && (
            <button type="button" onClick={() => setSheet("release")} className="flex min-h-[56px] w-full items-center gap-3 rounded-[16px] border border-[#E4E9F1] px-4 text-left active:bg-[#EEF3FA]">
              <Undo2 className="h-5 w-5 text-[#B91C1C]" />
              <span className="text-[15px] font-bold text-[#B91C1C]">{t("captain.modal.releaseTitle")}</span>
            </button>
          )}
        </div>
      </Sheet>

      <Sheet open={sheet === "late"} onClose={() => setSheet(null)} title={t("captain.modal.riskTitle")}>
        <p className="text-sm text-[#5F6878]">{t("captain.v2.lateHint")}</p>
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("captain.modal.riskPlaceholder")}
          className="mt-3 w-full rounded-2xl border border-[#E4E9F1] px-3.5 py-3 text-[15px] outline-none focus:border-[#0A66F0]"
        />
        {error && <p className="mt-2 text-sm font-medium text-[#B91C1C]" role="alert">{error}</p>}
        <Btn className="mt-3 w-full" loading={risk.isPending} onClick={() => risk.mutate()}>
          {t("captain.v2.notifyManager")}
        </Btn>
      </Sheet>

      <Sheet open={sheet === "release"} onClose={() => setSheet(null)} title={t("captain.modal.releaseTitle")}>
        <p className="text-sm text-[#5F6878]">{t("captain.v2.releaseHint")}</p>
        <textarea
          rows={3}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("captain.modal.releasePlaceholder")}
          className="mt-3 w-full rounded-2xl border border-[#E4E9F1] px-3.5 py-3 text-[15px] outline-none focus:border-[#0A66F0]"
        />
        {error && <p className="mt-2 text-sm font-medium text-[#B91C1C]" role="alert">{error}</p>}
        <Btn variant="danger" className="mt-3 w-full" disabled={note.trim().length < 3} loading={release.isPending} onClick={() => release.mutate()}>
          {t("captain.v2.releaseCta")}
        </Btn>
      </Sheet>
    </>
  );
}

/** Design screen 4: what's done, what's live, what's left — real times. */
function WashStepper({ car, now }: { car: Booking; now: number }) {
  const { t } = useCaptainTranslation();
  const elapsed = car.service_started_at ? Math.max(0, Math.round((now - new Date(car.service_started_at).getTime()) / 60000)) : 0;
  const estimate = car.duration_minutes || 60;
  const over = elapsed > estimate;
  const steps = [
    { label: t("captain.v2.step.arrived"), sub: clock(car.vehicle_verified_at), state: "done" },
    { label: t("captain.v2.step.washStarted"), sub: clock(car.service_started_at), state: "done" },
    {
      label: t("captain.v2.step.washing"),
      sub: t("captain.v2.step.timer").replace("{n}", String(elapsed)).replace("{est}", String(estimate)),
      state: "now",
    },
    { label: t("captain.v2.step.afterPhoto"), sub: "", state: "todo" },
    { label: t("captain.v2.step.complete"), sub: "", state: "todo" },
  ] as const;
  return (
    <ol>
      {steps.map((s, i) => (
        <li key={s.label} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full text-sm font-extrabold",
                s.state === "done" && "bg-[#16A34A] text-white",
                s.state === "now" && "bg-[#0A66F0] text-white ring-4 ring-[#E8F0FE]",
                s.state === "todo" && "border-2 border-[#E4E9F1] bg-white text-[#A3AAB6]",
              )}
            >
              {s.state === "done" ? <Check className="h-4 w-4" strokeWidth={3} /> : i + 1}
            </span>
            {i < steps.length - 1 && <span className={cn("my-1 w-0.5 flex-1", s.state === "done" ? "bg-[#16A34A]" : "bg-[#E4E9F1]")} />}
          </div>
          <div className="min-h-[52px] pb-3 pt-1">
            <p className={cn("text-[15px] font-bold", s.state === "todo" ? "text-[#A3AAB6]" : "text-[#0E1A33]")}>{s.label}</p>
            {s.sub && <p className={cn("text-sm font-medium", s.state === "now" && over ? "text-[#9A6400]" : "text-[#5F6878]")}>{s.sub}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}
