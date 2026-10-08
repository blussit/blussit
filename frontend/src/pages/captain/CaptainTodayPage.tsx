/**
 * Captain · Today — greeting, today's counts, the ONE next booking with
 * Navigate + its next step, and today's society duty when there is one.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Bell, CarFront, ChevronRight, Clock, Lock, MapPin, Navigation, ShieldAlert, TriangleAlert } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { kycApi } from "../../api/staffOps";
import { useAuth } from "../../context/AuthContext";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { todayIST } from "../../lib/date";
import { CaptainSocietiesToday } from "../../components/captain/society/CaptainSocietiesToday";
import { CaptainSocietyVisits } from "../../components/captain/society/CaptainSocietyVisits";
import { useActiveJobs, useUnreadNotifications } from "../../components/captain/CaptainShell";
import { useClock, useTodayAttendance } from "../../components/captain/Attendance";
import {
  blockedByFlag,
  currentCar,
  headOutOpensAt,
  istDay,
  istHour,
  mapsUrl,
  needsManager,
  pickNext,
  stepOf,
  useCarTypes,
  visitCarService,
  whenLabel,
  type Step,
} from "../../components/captain/jobState";
import { Btn, LoadError, Notice, Panel, StatTile } from "../../components/captain/ui";
import { toSlabs, type BookingSlab } from "../../lib/bookingGroups";

function countdown(ms: number, t: ReturnType<typeof useCaptainTranslation>["t"]): string {
  const mins = Math.max(0, Math.round(ms / 60000));
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return [h ? `${h} ${t("captain.job.hoursShort")}` : "", m || !h ? `${m} ${t("captain.job.minsShort")}` : ""].filter(Boolean).join(" ");
}

function stepCta(step: Step, t: ReturnType<typeof useCaptainTranslation>["t"]): string {
  return {
    start: t("captain.actions.startJob"),
    arrive: t("captain.v2.act.arrived"),
    before: t("captain.v2.act.beforePhoto"),
    after: t("captain.v2.act.afterPhoto"),
    done: t("captain.actions.viewDetails"),
    closed: t("captain.actions.viewDetails"),
  }[step];
}

function NextBookingCard({ slab }: { slab: BookingSlab }) {
  const { t } = useCaptainTranslation();
  const navigate = useNavigate();
  const [now, setNow] = useState(() => Date.now());
  const [lockHint, setLockHint] = useState(false);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const types = useCarTypes(slab.bookings);
  const car = currentCar(slab);
  const step = stepOf(car);
  const reached = slab.bookings.some((c) => c.vehicle_verified || c.service_started_at || c.status === "completed");
  // Directions exist for the ride only: unlocked by "Start trip" (that
  // tap records his departure), gone once he's at the car.
  const canNavigate = car.status === "captain_on_the_way" && !reached;
  const url = mapsUrl(car);
  const opensAt = step === "start" ? headOutOpensAt(slab.bookings) : null;
  // "Sedan · Star Wash" / "2 × Sedan · Star Wash" — car type, then service.
  const what = visitCarService(slab.bookings, types);
  const flagged = blockedByFlag(car);

  return (
    <Panel className="overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-4">
        <p className="text-[13px] font-bold uppercase tracking-wide text-[#5F6878]">{t("captain.v2.nextBooking")}</p>
        <Link to="/captain/jobs" className="flex min-h-[40px] items-center gap-0.5 text-sm font-bold text-[#0A66F0]">
          {t("captain.v2.allJobs")} <ChevronRight className="h-4 w-4" />
        </Link>
      </div>
      <button type="button" onClick={() => navigate(`/captain/jobs/${car.id}`)} className="block w-full px-4 pb-3 text-left">
        <p className="flex items-center gap-2 text-[20px] font-extrabold text-[#0E1A33]">
          <Clock className="h-5 w-5 text-[#0A66F0]" />
          {whenLabel(car, { today: t("captain.v2.today"), tomorrow: t("captain.v2.tomorrow") })}
        </p>
        <p className="mt-2 flex items-center gap-2 text-[15px] font-semibold text-[#0E1A33]">
          <CarFront className="h-4 w-4 shrink-0 text-[#5F6878]" />
          <span className="truncate">{what}</span>
        </p>
        <p className="mt-1 flex items-center gap-2 text-sm text-[#5F6878]">
          <MapPin className="h-4 w-4 shrink-0" />
          <span className="truncate">
            {car.address_snapshot?.line1 || "—"}
            {car.travel_distance_km != null && ` · ${t("captain.v2.kmAway").replace("{km}", String(car.travel_distance_km))}`}
          </span>
        </p>
        {opensAt && opensAt > now && (
          <p className="mt-2 text-sm font-semibold text-[#9A6400]">{t("captain.v2.startsIn").replace("{time}", countdown(opensAt - now, t))}</p>
        )}
      </button>
      {flagged && (
        <div className="px-4 pb-3">
          <Notice tone="amber" icon={<TriangleAlert className="h-4 w-4" />}>{t("captain.v2.flagged")}</Notice>
        </div>
      )}
      {lockHint && (
        <p className="px-4 pb-2 text-sm font-medium text-[#5F6878]">{t("captain.job.navigateLockedTip")}</p>
      )}
      <div className="grid grid-cols-2 gap-2 border-t border-[#E4E9F1] p-3">
        {canNavigate && url ? (
          <a href={url} target="_blank" rel="noreferrer" className="inline-flex min-h-[52px] items-center justify-center gap-2 rounded-2xl bg-[#E8F0FE] text-[15px] font-bold text-[#0A66F0]">
            <Navigation className="h-5 w-5" /> {t("captain.job.navigate")}
          </a>
        ) : (
          <Btn variant="secondary" disabled={reached} onClick={() => setLockHint((v) => !v)} className={reached ? "" : "opacity-80"}>
            {reached ? <MapPin className="h-5 w-5" /> : <Lock className="h-4 w-4" />} {reached ? t("captain.v2.status.arrived") : t("captain.job.navigate")}
          </Btn>
        )}
        <Btn onClick={() => navigate(`/captain/jobs/${car.id}`, { state: step === "arrive" ? { open: "arrive" } : undefined })}>
          {stepCta(step, t)}
        </Btn>
      </div>
    </Panel>
  );
}

export default function CaptainTodayPage() {
  const { t, language } = useCaptainTranslation();
  const { user } = useAuth();
  const active = useActiveJobs();
  const recent = useQuery({
    queryKey: ["my-jobs", "history", "recent"],
    queryFn: () => bookingApi.myJobs({ scope: "history", status: "completed", page: 1, page_size: 100 }),
  });
  const { data: kyc } = useQuery({ queryKey: ["my-kyc"], queryFn: kycApi.my });
  const attendanceQuery = useTodayAttendance();
  const { today: attendance, isLoading: attLoading } = attendanceQuery;
  // A failed read must not read as "not clocked in" (and invite a punch),
  // nor as zero jobs in the tiles.
  const attFailed = attendanceQuery.isError && !attendanceQuery.data;
  const activeFailed = active.isError && !active.data;
  const recentFailed = recent.isError && !recent.data;
  const { clockIn, error: clockError } = useClock();
  const { data: notifs } = useUnreadNotifications();

  const todayKey = todayIST();
  const jobs = active.data?.data ?? [];
  const remaining = jobs.filter((j) => j.scheduled_date.slice(0, 10) <= todayKey).length;
  const completed = (recent.data?.data ?? []).filter((j) => istDay(j.completed_at) === todayKey).length;
  const next = pickNext(jobs);
  const stuck = jobs.filter((j) => needsManager(j)).length;
  const later = next ? toSlabs(jobs).filter((s) => s.key !== next.key && !needsManager(currentCar(s))).length : 0;

  const hour = istHour();
  const hello = hour < 12 ? t("captain.v2.goodMorning") : hour < 17 ? t("captain.v2.goodAfternoon") : t("captain.v2.goodEvening");
  const firstName = (user?.full_name || "").trim().split(/\s+/)[0] || "";
  const dateLine = new Date().toLocaleDateString(language === "hi" ? "hi-IN" : "en-IN", { weekday: "long", day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 pt-5">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-[#5F6878]">{t("captain.v2.today")} · {dateLine}</p>
          <h1 className="text-[24px] font-extrabold leading-tight text-[#0E1A33]">
            {hello}, {firstName} 👋
          </h1>
        </div>
        <Link to="/captain/notifications" aria-label={t("captain.v2.notifications")} className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-[#E4E9F1] text-[#0E1A33]">
          <Bell className="h-5 w-5" />
          {!!notifs?.unread_count && <span className="absolute right-2.5 top-2.5 h-2.5 w-2.5 rounded-full bg-[#DC2626] ring-2 ring-white" />}
        </Link>
      </div>

      {attFailed ? (
        <Panel className="flex items-center gap-3 p-3 pl-4">
          <p role="alert" className="min-w-0 flex-1 text-sm font-semibold text-[#0E1A33]">Couldn't load your attendance.</p>
          <Btn
            variant="secondary"
            className="!min-h-[44px] shrink-0 px-4 text-sm"
            loading={attendanceQuery.isFetching}
            onClick={() => void attendanceQuery.refetch()}
          >
            {t("captain.v2.tryAgain")}
          </Btn>
        </Panel>
      ) : !attLoading && !attendance && (
        <Panel className="flex items-center gap-3 p-3 pl-4">
          <p className="min-w-0 flex-1 text-sm font-semibold text-[#0E1A33]">{t("captain.v2.att.notIn")}</p>
          <Btn className="!min-h-[44px] shrink-0 px-4 text-sm" loading={clockIn.isPending} onClick={() => clockIn.mutate()}>
            {t("captain.v2.att.clockIn")}
          </Btn>
        </Panel>
      )}
      {clockError && <p className="text-sm font-medium text-[#B91C1C]">{clockError}</p>}

      {kyc && (kyc.status === "pending" || kyc.status === "rejected") && (
        <Link to="/captain/profile" className="block">
          <Notice tone="amber" icon={<ShieldAlert className="h-4 w-4" />}>
            {kyc.status === "rejected" ? t("captain.v2.kycRejected") : t("captain.v2.kycPending")}
          </Notice>
        </Link>
      )}

      <div className="grid grid-cols-3 gap-2">
        <StatTile label={t("captain.v2.stat.total")} value={activeFailed || recentFailed ? "—" : remaining + completed} />
        <StatTile label={t("captain.v2.stat.completed")} value={recentFailed ? "—" : completed} tone="green" />
        <StatTile label={t("captain.v2.stat.remaining")} value={activeFailed ? "—" : remaining} tone="blue" />
      </div>
      {recentFailed && (
        <p role="alert" className="text-sm text-[#5F6878]">
          Couldn't load today's finished jobs.{" "}
          <button type="button" className="font-bold text-[#0A66F0] disabled:opacity-60" disabled={recent.isFetching} onClick={() => void recent.refetch()}>
            {t("captain.v2.tryAgain")}
          </button>
        </p>
      )}

      {active.isLoading ? (
        <Panel className="h-56 animate-pulse bg-[#EEF3FA]" ><span /></Panel>
      ) : activeFailed ? (
        <LoadError
          title={t("captain.v2.loadFailed")}
          sub={t("captain.v2.loadFailedSub")}
          retryLabel={t("captain.v2.tryAgain")}
          busy={active.isFetching}
          onRetry={() => void active.refetch()}
        />
      ) : next ? (
        <NextBookingCard slab={next} />
      ) : stuck > 0 ? (
        // Every job left is waiting on the manager (e.g. its window was
        // missed) — "No jobs" beside "N remaining" would contradict itself.
        <Panel className="px-4 py-8 text-center">
          <p className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.allWaiting")}</p>
          <p className="mt-1 text-sm text-[#5F6878]">{t("captain.v2.allWaitingSub")}</p>
        </Panel>
      ) : (
        <Panel className="px-4 py-8 text-center">
          <p className="text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.noJobs")}</p>
          <p className="mt-1 text-sm text-[#5F6878]">{t("captain.v2.noJobsSub")}</p>
        </Panel>
      )}

      {later > 0 && (
        <Link to="/captain/jobs" className="flex min-h-[52px] items-center justify-between rounded-[16px] border border-[#E4E9F1] px-4 text-[15px] font-semibold text-[#0E1A33]">
          {t("captain.v2.moreJobs").replace("{n}", String(later))} <ChevronRight className="h-5 w-5 text-[#A3AAB6]" />
        </Link>
      )}
      {stuck > 0 && (
        <Link to="/captain/jobs" className="block">
          <Notice tone="gray">{t("captain.v2.waitingManager").replace("{n}", String(stuck))}</Notice>
        </Link>
      )}

      <CaptainSocietyVisits language={language === "hi" ? "hi" : "en"} hideWhenEmpty />
      <CaptainSocietiesToday language={language === "hi" ? "hi" : "en"} hideWhenEmpty />
    </div>
  );
}
