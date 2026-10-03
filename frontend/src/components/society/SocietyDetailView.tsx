/**
 * One society, for its manager and for admin (same view, the server scopes
 * who may open it): the shareable form link, the daily captain, residents
 * with their plans and usage, the attendance calendar and payments.
 */
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CalendarCheck2, ChevronLeft, ChevronRight, Copy, MapPin, MessageCircle, Pencil, RefreshCw, UserPlus } from "lucide-react";
import { Badge, Button, Input, Panel, Select, Spinner, StatCard, StatusBadge } from "../ui";
import { useToast } from "../../context/ToastContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatShortDate, todayIST } from "../../lib/date";
import { titleCase } from "../public/landing/shared";
import {
  bucketUsage,
  carLabel,
  clockIST,
  planWithServices,
  premiumUsage,
  rupees,
  societyApi,
  societyFormUrl,
  societyIssueApi,
  whatsappShareUrl,
  type AttendanceMonth,
  type SocietyDetail,
  type SocietyEnrollment,
} from "../../api/society";
import { ComplaintDetailDrawer } from "../shared/ComplaintDetailDrawer";
import type { Complaint } from "../../types";
import { SocietyFormModal } from "./SocietyFormModal";
import { AddResidentModal, CollectCashModal, StaffBookPremiumModal } from "./SocietyResidentModals";
// Premium-wash schedule (docs/SOCIETY_PLANS.md §9) — self-contained tab + resident-row button.
import { ResidentScheduleButton, SocietyScheduleTab } from "./schedule/SocietyScheduleTab";

const STATUS: Record<SocietyEnrollment["status"], { label: string; tone: "warning" | "error" | "success" | "neutral" }> = {
  requested: { label: "Request", tone: "warning" },
  awaiting_payment: { label: "Awaiting Payment", tone: "error" },
  active: { label: "Active", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function SocietyDetailView({ societyId, backTo, isAdmin = false }: { societyId: string; backTo: string; isAdmin?: boolean }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<"residents" | "schedule" | "issues" | "attendance" | "payments">(searchParams.get("tab") === "schedule" ? "schedule" : "residents");
  const [collect, setCollect] = useState<{ enrollment: SocietyEnrollment; mode: "activate" | "renew" } | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [editOpen, setEditOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [bookFor, setBookFor] = useState<SocietyEnrollment | null>(null);
  const society = useQuery({ queryKey: ["society", societyId], queryFn: () => societyApi.get(societyId) });
  const enrollments = useQuery({ queryKey: ["society-enrollments", societyId, filter], queryFn: () => societyApi.enrollments(societyId, filter || undefined) });
  const captains = useQuery({
    queryKey: ["society-captains", society.data?.service_center_id],
    queryFn: () => societyApi.captains(isAdmin ? society.data?.service_center_id : undefined),
    enabled: !!society.data,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["society", societyId] });
    queryClient.invalidateQueries({ queryKey: ["society-enrollments", societyId] });
    queryClient.invalidateQueries({ queryKey: ["society-attendance", societyId] });
    queryClient.invalidateQueries({ queryKey: ["societies"] });
    queryClient.invalidateQueries({ queryKey: ["society-issues", societyId] });
  };
  const ok = (title: string) => toast.push({ tone: "success", title });
  const fail = (err: unknown) => toast.push({ tone: "error", title: "That didn't work", message: getErrorMessage(err) });

  const act = useMutation({
    mutationFn: async ({ id, vehicleIds }: { kind: "cancel"; id: string; vehicleIds?: string[] }) => societyApi.cancel(id, vehicleIds),
    onSuccess: () => { ok("Cancelled"); refresh(); },
    onError: (err) => { fail(err); refresh(); },
  });
  const linkAct = useMutation({
    mutationFn: async (kind: "rotate" | "toggle") =>
      kind === "rotate" ? societyApi.rotateLink(societyId) : societyApi.update(societyId, { form_enabled: !society.data?.form_enabled }),
    onSuccess: (_d, kind) => { ok(kind === "rotate" ? "New link created" : "Saved"); refresh(); },
    onError: fail,
  });
  const captainAct = useMutation({
    mutationFn: (p: { captain_id: string | null; date?: string }) => societyApi.setCaptain(societyId, p),
    onSuccess: () => { ok("Captain saved"); refresh(); },
    onError: fail,
  });

  if (society.isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (!society.data) return <p className="py-10 text-center text-sm text-gray-500">{getErrorMessage(society.error) || "Society not found."}</p>;
  const s = society.data;
  const url = societyFormUrl(s.form_path);
  const share = `Hi! Blussit now washes cars daily at ${s.name}. Pick your plan here: ${url}`;

  return (
    <div className="space-y-5">
      <div>
        <Link to={backTo} className="mb-2 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-black"><ArrowLeft className="h-4 w-4" /> Societies</Link>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-display text-2xl font-bold text-black">{s.name}</h1>
            <p className="mt-0.5 flex items-center gap-1 text-sm text-gray-500"><MapPin className="h-4 w-4 shrink-0" /> <span className="truncate">{[s.address_line, s.area, s.city, s.pincode].filter(Boolean).join(", ")}</span></p>
            <p className="mt-0.5 text-xs text-gray-400">{s.service_center_name}{s.contact_name ? ` · ${s.contact_name}${s.contact_phone ? ` (${s.contact_phone})` : ""}` : ""}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}><Pencil className="h-4 w-4" /> Edit</Button>
            <Button size="sm" onClick={() => setAddOpen(true)}><UserPlus className="h-4 w-4" /> Add Resident</Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Residents" value={s.residents} hint="active plans" />
        <StatCard label="Active Cars" value={s.active_cars} />
        <StatCard label="Requests" value={s.requests} tone={s.requests ? "warning" : "default"} hint="waiting for you" />
        <button type="button" className="text-left" onClick={() => setTab("issues")} data-testid="society-open-issues">
          <StatCard label="Open Issues" value={s.open_issues ?? 0} tone={s.open_issues ? "warning" : "default"} hint="from residents" icon={AlertTriangle} />
        </button>
        <StatCard label="Today" value={s.today_attendance ? "Present" : "—"} tone={s.today_attendance ? "success" : "muted"}
          hint={s.today_attendance ? `Arrived ${clockIST(s.today_attendance.arrived_at)} · ${s.today_attendance.washed_count} washed${s.today_attendance.far_from_society ? " · far from pin" : ""}` : "no arrival yet"} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <Panel title="Resident Form Link" description={s.form_enabled ? "Share it in the society group." : "Switched off — residents can't open it."}>
          <div className="flex items-center gap-2 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] px-3 py-2">
            <code className="min-w-0 flex-1 truncate text-xs" data-testid="society-link">{url}</code>
            <button type="button" aria-label="Copy link" className="shrink-0 rounded-lg p-1.5 hover:bg-white"
              onClick={() => navigator.clipboard?.writeText(url).then(() => ok("Link copied")).catch(() => undefined)}>
              <Copy className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <a href={whatsappShareUrl(share)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-full bg-[#25D366] px-3.5 py-1.5 text-sm font-semibold text-white"><MessageCircle className="h-4 w-4" /> Share On WhatsApp</a>
            <Button variant="outline" size="sm" isLoading={linkAct.isPending && linkAct.variables === "rotate"}
              onClick={async () => { if (await confirm({ title: "Create A New Link?", message: "The old link stops working at once.", confirmLabel: "Create Link" })) linkAct.mutate("rotate"); }}>
              <RefreshCw className="h-4 w-4" /> New Link
            </Button>
            <Button variant="ghost" size="sm" onClick={() => linkAct.mutate("toggle")}>{s.form_enabled ? "Switch Off" : "Switch On"}</Button>
          </div>
        </Panel>
        <CaptainPanel society={s} captains={captains.data || []} saving={captainAct.isPending} onSave={(p) => captainAct.mutate(p)} />
      </div>

      <div className="flex max-w-full gap-1 overflow-x-auto rounded-full bg-gray-100 p-1 text-sm font-semibold sm:w-fit">
        {(["residents", "schedule", "issues", "attendance", "payments"] as const).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`flex-1 shrink-0 whitespace-nowrap rounded-full px-3 py-1.5 capitalize sm:flex-none sm:px-4 ${tab === t ? "bg-white text-black shadow-sm" : "text-gray-500"}`}>
            {t}{t === "issues" && s.open_issues ? <span className="ml-1 rounded-full bg-[#FFD21F] px-1.5 text-[11px] text-[#0E1A33]">{s.open_issues}</span> : null}
          </button>
        ))}
      </div>

      {tab === "residents" && (
        <Panel title="Residents" actions={
          <Select aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All</option>
            <option value="open">Requests</option>
            <option value="active">Active</option>
            <option value="cancelled">Cancelled</option>
          </Select>
        }>
          {enrollments.isLoading ? <Spinner /> : !enrollments.data?.length ? (
            <p className="py-6 text-center text-sm text-gray-500"><b className="block text-black">No Residents Yet</b>Share the link or add one.</p>
          ) : (
            <ul className="divide-y divide-[#F3E5B5]">
              {enrollments.data.map((e) => (
                <li key={e.id} className="py-3" data-testid="society-resident">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-semibold text-black">{e.resident_name} <span className="font-normal text-gray-500">· {e.flat} · {e.phone}</span></p>
                      <p className="text-xs text-gray-500">
                        <span className="font-medium text-gray-700">{planWithServices(e)}</span> · {rupees(e.total_amount)}/month
                        {e.coupon_code && e.discount_amount > 0 && e.status !== "active" ? ` · coupon ${e.coupon_code} −${rupees(e.discount_amount)}` : ""}
                        {e.payment ? ` · paid ${rupees(e.payment.amount)} ${e.payment.method}${e.payment.discount ? ` (coupon ${e.payment.coupon_code} −${rupees(e.payment.discount)})` : ""}` : ""}
                      </p>
                    </div>
                    <Badge tone={STATUS[e.status].tone}>{STATUS[e.status].label}</Badge>
                  </div>
                  <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                    {e.cars.map((c) => (
                      <div key={c.vehicle_id} className="flex items-center justify-between gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs">
                        <span className="min-w-0">
                          <span className="block font-mono font-semibold">{c.registration_number}</span>
                          {c.vehicle_type_name ? <span className="block text-gray-500">{titleCase(c.vehicle_type_name)}</span> : null}
                        </span>
                        <span className="text-right text-gray-600">
                          {c.subscription && c.status === "active"
                            ? <>
                                <span title="Premium washes left / this cycle">{premiumUsage(e.premium_service_name, c.subscription.remaining, c.subscription.total)}</span>
                                {" · "}
                                <span title="Daily washes done / allowed this cycle">{bucketUsage(e.bucket_short_label, c.bucket_used ?? 0, c.bucket_allowance)}</span>
                                {c.subscription.end_date ? ` · Till ${formatShortDate(c.subscription.end_date.slice(0, 10))}` : ""}
                              </>
                            : titleCase(c.status)}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {(e.status === "requested" || e.status === "awaiting_payment") && (
                      <Button size="sm" variant="info" onClick={() => setCollect({ enrollment: e, mode: "activate" })}>
                        Mark Paid (Cash)
                      </Button>
                    )}
                    {e.status === "active" && (
                      <Button size="sm" variant="outline" onClick={() => setBookFor(e)}><CalendarCheck2 className="h-4 w-4" /> Book Premium Wash</Button>
                    )}
                    {e.status === "active" && <ResidentScheduleButton societyId={societyId} enrollmentId={e.id} />}
                    {e.renew_open && (
                      <Button size="sm" variant="outline" onClick={() => setCollect({ enrollment: e, mode: "renew" })}>
                        Renew (Cash)
                      </Button>
                    )}
                    {e.status !== "cancelled" && (
                      <Button size="sm" variant="ghost" onClick={async () => { if (await confirm({ title: "Cancel This Plan?", message: "No refund is made automatically.", tone: "danger", confirmLabel: "Cancel Plan", cancelLabel: "Keep" })) act.mutate({ kind: "cancel", id: e.id }); }}>
                        Cancel
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
      {tab === "schedule" && <SocietyScheduleTab societyId={societyId} plannerPath={backTo.replace(/\/societies$/, "/society-planner")} />}
      {tab === "issues" && <IssuesPanel societyId={societyId} />}
      {tab === "attendance" && <AttendancePanel societyId={societyId} />}
      {tab === "payments" && (
        <Panel title="Payments">
          {!s.payments?.length ? <p className="py-6 text-center text-sm font-semibold text-black">No Payments Yet</p> : (
            <ul className="divide-y divide-[#F3E5B5] text-sm">
              {s.payments.map((p) => (
                <li key={p.id} className="flex items-start justify-between gap-3 py-2">
                  <span className="min-w-0">
                    <span className="block">{[p.resident_name, p.flat].filter(Boolean).join(" · ")} · {titleCase(p.kind)} · {p.car_count} Car{p.car_count === 1 ? "" : "s"} <span className="text-gray-400">· {formatShortDate(p.created_at.slice(0, 10))}</span></span>
                    {p.plan_services ? <span className="block text-xs font-medium text-gray-600">{titleCase(p.plan_services)}</span> : null}
                    {p.cars?.length ? (
                      <span className="block text-xs text-gray-500">{p.cars.map((c) => carLabel(c.registration_number, c.vehicle_type_name)).join(", ")}</span>
                    ) : null}
                  </span>
                  <span className="shrink-0 font-semibold">{rupees(p.amount)} <span className="text-xs font-normal text-gray-500">{titleCase(p.method)}</span></span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <SocietyFormModal open={editOpen} onClose={() => setEditOpen(false)} society={s} isAdmin={isAdmin} onSaved={() => { setEditOpen(false); ok("Saved"); refresh(); }} />
      <AddResidentModal open={addOpen} onClose={() => setAddOpen(false)} societyId={societyId} onSaved={() => { setAddOpen(false); ok("Resident added"); refresh(); }} />
      <CollectCashModal open={!!collect} onClose={() => setCollect(null)} enrollment={collect?.enrollment ?? null} mode={collect?.mode ?? "activate"}
        onDone={(msg) => { setCollect(null); ok(msg); refresh(); }} />
      <StaffBookPremiumModal open={!!bookFor} onClose={() => setBookFor(null)} societyId={societyId} centerId={s.service_center_id} enrollment={bookFor}
        onBooked={(msg) => { setBookFor(null); ok(msg); refresh(); }} />
    </div>
  );
}

/** Residents' issues for this society — the same tickets as the
 * Complaints page, filtered to this society. */
function IssuesPanel({ societyId }: { societyId: string }) {
  const [status, setStatus] = useState("open");
  const [selected, setSelected] = useState<Complaint | null>(null);
  const q = useQuery({ queryKey: ["society-issues", societyId, status], queryFn: () => societyIssueApi.forSociety(societyId, status || undefined) });
  return (
    <Panel title="Resident Issues" description="Raised from the society page. Reply and resolve here." actions={
      <Select aria-label="Issue status" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="open">Open</option>
        <option value="resolved">Resolved</option>
        <option value="closed">Closed</option>
        <option value="">All</option>
      </Select>
    }>
      {q.isLoading ? <Spinner /> : !q.data?.length ? (
        <p className="py-6 text-center text-sm font-semibold text-black">{status === "open" ? "No Open Issues" : "Nothing Here"}</p>
      ) : (
        <ul className="divide-y divide-[#E4E9F1]" data-testid="society-issues">
          {q.data.map((i) => (
            <li key={i.id}>
              <button type="button" className="flex w-full items-start justify-between gap-3 py-3 text-left hover:bg-[#F7F9FC]" onClick={() => setSelected(i as unknown as Complaint)}>
                <span className="min-w-0">
                  <span className="block font-semibold text-black">{titleCase(i.issue_label)}</span>
                  <span className="block truncate text-xs text-gray-500">{[i.customer_name, i.flat, i.registration_number, titleCase(i.vehicle_type_name)].filter(Boolean).join(" · ")} · {format(i.created_at)}</span>
                  {i.description && i.description !== `${i.issue_label}.` && <span className="mt-0.5 block truncate text-xs text-gray-600">{i.description}</span>}
                </span>
                <StatusBadge status={i.status} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <ComplaintDetailDrawer complaint={selected} onClose={() => { setSelected(null); void q.refetch(); }} onUpdated={(c) => setSelected(c)} />
    </Panel>
  );
}

function CaptainPanel({ society, captains, saving, onSave }: {
  society: SocietyDetail; captains: { id: string; name?: string | null }[]; saving: boolean;
  onSave: (p: { captain_id: string | null; date?: string }) => void;
}) {
  const [subDate, setSubDate] = useState(todayIST());
  const [subCaptain, setSubCaptain] = useState("");
  return (
    <Panel title="Daily Captain" description="Marks arrival every day and ticks the cars washed.">
      <Select aria-label="Daily captain" value={society.daily_captain?.id || ""} onChange={(e) => onSave({ captain_id: e.target.value || null })} disabled={saving}>
        <option value="">Not Assigned</option>
        {captains.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </Select>
      <p className="mt-3 text-xs font-medium text-gray-500">Captain Away? Set A Substitute For One Day</p>
      <div className="mt-1.5 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
        <Input type="date" value={subDate} min={todayIST()} onChange={(e) => setSubDate(e.target.value)} aria-label="Substitute date" />
        <Select aria-label="Substitute captain" value={subCaptain} onChange={(e) => setSubCaptain(e.target.value)}>
          <option value="">Pick Captain</option>
          {captains.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
        <Button variant="outline" disabled={!subCaptain || saving} onClick={() => onSave({ captain_id: subCaptain, date: subDate })}>Set</Button>
      </div>
      {society.substitute && (
        <p className="mt-2 text-xs text-gray-500">
          {society.substitute.captain?.name} covers {formatShortDate(society.substitute.date)}.{" "}
          <button type="button" className="font-semibold text-black underline" onClick={() => onSave({ captain_id: null, date: society.substitute!.date })}>Remove</button>
        </p>
      )}
    </Panel>
  );
}

function shiftMonth(month: string, by: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + by, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function AttendancePanel({ societyId }: { societyId: string }) {
  const [month, setMonth] = useState(todayIST().slice(0, 7));
  const q = useQuery({ queryKey: ["society-attendance", societyId, month], queryFn: () => societyApi.attendance(societyId, month) });
  return (
    <Panel title="Attendance" actions={
      <div className="flex items-center gap-1">
        <button type="button" aria-label="Previous month" className="rounded-full p-1.5 hover:bg-gray-100" onClick={() => setMonth((m) => shiftMonth(m, -1))}><ChevronLeft className="h-4 w-4" /></button>
        <span className="w-20 text-center text-sm font-semibold">{new Date(`${month}-15T12:00:00+05:30`).toLocaleDateString("en-IN", { month: "short", year: "numeric" })}</span>
        <button type="button" aria-label="Next month" className="rounded-full p-1.5 hover:bg-gray-100" onClick={() => setMonth((m) => shiftMonth(m, 1))}><ChevronRight className="h-4 w-4" /></button>
      </div>
    }>
      {q.isLoading || !q.data ? <Spinner /> : <AttendanceCalendar data={q.data} />}
    </Panel>
  );
}

export function AttendanceCalendar({ data }: { data: AttendanceMonth }) {
  const lead = useMemo(() => {
    const first = new Date(`${data.days[0]?.date}T12:00:00+05:30`);
    return (first.getUTCDay() + 6) % 7; // Monday-first grid
  }, [data.days]);
  const [picked, setPicked] = useState<string | null>(null);
  const day = data.days.find((d) => d.date === picked);
  return (
    <div>
      <p className="mb-3 text-sm text-gray-600">
        <b className="text-black">{data.present_days}</b> Day{data.present_days === 1 ? "" : "s"} Present · <b className="text-black">{data.missed_days}</b> Missed · <b className="text-black">{data.washes}</b> Car Wash{data.washes === 1 ? "" : "es"}
      </p>
      <div className="max-w-md">
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-gray-400">
        {["M", "T", "W", "T", "F", "S", "S"].map((d, i) => <span key={i}>{d}</span>)}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-1" data-testid="attendance-calendar">
        {Array.from({ length: lead }, (_, i) => <span key={`b${i}`} />)}
        {data.days.map((d) => (
          <button key={d.date} type="button" onClick={() => setPicked(d.date)} title={d.date}
            className={`flex aspect-square flex-col items-center justify-center rounded-lg text-xs font-semibold transition ${
              d.present ? (d.far_from_society || d.location_missing ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800")
                : d.missed ? "bg-red-50 text-red-600" : d.future ? "text-gray-300" : "bg-gray-50 text-gray-500"
            } ${picked === d.date ? "ring-2 ring-black" : ""}`}>
            {Number(d.date.slice(8))}
            {d.present && <span className="text-[9px] font-normal">{d.washed_count}</span>}
          </button>
        ))}
      </div>
      </div>
      {day && (
        <p className="mt-3 rounded-lg bg-gray-50 px-3 py-2 text-sm">
          {formatShortDate(day.date)}:{" "}
          {day.present
            ? <>{day.captain_name} arrived {clockIST(day.arrived_at)}, {day.washed_count} car{day.washed_count === 1 ? "" : "s"} washed{day.far_from_society ? ` · ${day.distance_m} m from the pin` : ""}{day.location_missing ? " · no location" : ""}</>
            : day.missed ? "No attendance." : day.future ? "Upcoming." : "Not tracked."}
        </p>
      )}
    </div>
  );
}
