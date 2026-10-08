/**
 * Society detail → Schedule: when captains come for the residents' premium
 * washes (repeat visit days + one-offs), each upcoming day with its cars,
 * residents' own repeat washes, change requests and who still has premium
 * washes without a date this plan month. Manager (own center) and admin.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, CalendarPlus, Repeat, UserRound } from "lucide-react";
import { ErrorState, Spinner } from "../../ui";
import { useToast } from "../../../context/ToastContext";
import { useConfirm } from "../../../context/ConfirmContext";
import { getErrorMessage } from "../../../lib/api-client";
import { formatShortDate } from "../../../lib/date";
import { scheduleApi, type ScheduleRule, type SocietySchedule } from "../../../api/societySchedule";
import { AddVisitModal, RequestList, ResidentRuleModal, SocietyRuleModal } from "./ScheduleModals";
import { VisitDayCard } from "./VisitDayCard";
import { Banner, Btn, Section } from "./scheduleUi";
import { carLabel } from "../../../api/society";
import { titleCase } from "../../public/landing/shared";

type Resident = SocietySchedule["residents"][number];

export function SocietyScheduleTab({ societyId, plannerPath }: { societyId: string; plannerPath?: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: ["society-schedule", societyId], queryFn: () => scheduleApi.society(societyId) });
  const [ruleModal, setRuleModal] = useState<{ rule: ScheduleRule | null } | null>(null);
  const [addVisit, setAddVisit] = useState(false);
  const [residentRule, setResidentRule] = useState<{ resident: Resident; rule: ScheduleRule | null } | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["society-schedule", societyId] });
    queryClient.invalidateQueries({ queryKey: ["society-planner"] });
  };
  const ok = (title: string) => { toast.push({ tone: "success", title }); refresh(); };

  if (q.isLoading) return <div className="flex justify-center py-10"><Spinner /></div>;
  if (!q.data) return <ErrorState message="Couldn't load the schedule." onRetry={() => void q.refetch()} busy={q.isFetching} />;
  const s = q.data;
  const upcoming = s.visits.filter((v) => v.date >= s.today && v.status !== "cancelled");
  const residentsWithout = s.residents.filter((r) => !s.resident_rules.some((rr) => rr.enrollment_id === r.enrollment_id && rr.is_active));
  const atRisk = s.outlook.filter((o) => o.at_risk > 0);

  return (
    <div className="space-y-4" data-testid="society-schedule">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-[17px] font-bold text-[#0E1A33]">Premium Wash Schedule</h2>
          <p className="text-[13px] text-[#5F6878]">
            Visit days book the residents' Star Wash / Deep Cleaning automatically {s.settings.generate_days_ahead} day{s.settings.generate_days_ahead === 1 ? "" : "s"} ahead — they get the normal booking confirmation.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {plannerPath && <Link to={plannerPath} className="inline-flex min-h-[36px] items-center gap-1.5 rounded-[10px] px-3 text-[13px] font-semibold text-[#0A66F0] hover:bg-[#E8F0FE]"><CalendarDays className="h-4 w-4" /> Center Planner</Link>}
          <Btn onClick={() => setAddVisit(true)}><CalendarPlus className="h-4 w-4" /> Add Visit Day</Btn>
          <Btn kind="primary" onClick={() => setRuleModal({ rule: null })}><Repeat className="h-4 w-4" /> Repeat Visit Day</Btn>
        </div>
      </div>

      {s.warnings.length > 0 && (
        <div className="space-y-1.5">{s.warnings.map((w, i) => <Banner key={i} tone="warn">{w.message}</Banner>)}</div>
      )}

      <Section title="Repeat Visit Days" description="When captains come to this society for premium washes.">
        {!s.rules.filter((r) => r.is_active).length ? (
          <p className="text-[13px] text-[#5F6878]">No repeat yet — add one (e.g. every 2 weeks on Saturday), or build a rotation for all societies from the center planner.</p>
        ) : (
          <ul className="divide-y divide-[#EEF2F7]">
            {s.rules.filter((r) => r.is_active).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5" data-testid="schedule-rule">
                <span className="min-w-0 text-[13px]">
                  <b className="text-[14px] text-[#0E1A33]">{titleCase(r.label)}</b>
                  <span className="block text-[#5F6878]">
                    {r.window_label} · {r.captains.map((c) => c.name).join(", ") || "Captains From The Queue"} · {r.washes_per_captain} Washes Each
                    {r.end_date ? ` · Until ${formatShortDate(r.end_date)}` : ""}
                  </span>
                </span>
                <span className="flex gap-2">
                  <Btn onClick={() => setRuleModal({ rule: r })}>Edit</Btn>
                  <Btn kind="danger" onClick={async () => {
                    if (!(await confirm({ title: "Remove This Repeat?", message: "Upcoming days not booked yet go; booked days stay.", confirmLabel: "Remove", tone: "danger" }))) return;
                    try { await scheduleApi.deleteRule(r.id); ok("Repeat removed"); } catch (err) { toast.push({ tone: "error", title: "That didn't work", message: getErrorMessage(err) }); }
                  }}>Remove</Btn>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Upcoming Days" description={`${upcoming.length} in the next ${Math.round((Date.parse(s.end) - Date.parse(s.today)) / 86400000) + 1} days`}>
        {!upcoming.length ? <p className="text-[13px] font-semibold text-[#0E1A33]">Nothing Planned</p> : (
          <div className="space-y-2.5">
            {upcoming.map((v) => <VisitDayCard key={v.id} visit={v} slots={s.slots} captains={s.captains} onChanged={refresh} />)}
          </div>
        )}
      </Section>

      <div className="grid gap-4 lg:grid-cols-2 [&>*]:min-w-0">
        <Section title="Residents' Repeat Washes" description="A resident's own fixed day — e.g. every Saturday morning.">
          {s.resident_rules.filter((r) => r.is_active).length > 0 && (
            <ul className="mb-3 divide-y divide-[#EEF2F7]">
              {s.resident_rules.filter((r) => r.is_active).map((r) => {
                const resident = s.residents.find((x) => x.enrollment_id === r.enrollment_id);
                return (
                  <li key={r.id} className="flex items-center justify-between gap-2 py-2 text-[13px]">
                    <span className="min-w-0">
                      <b className="text-[#0E1A33]">{resident?.resident_name || "Resident"} · {resident?.flat}</b>
                      <span className="block text-[#5F6878]">{titleCase(r.label)} · {r.window_label}{r.captains[0]?.name ? ` · ${r.captains[0].name}` : ""}</span>
                      {resident?.cars.length ? (
                        <span className="block text-[12px] text-[#5F6878]">
                          {resident.cars.filter((c) => !r.subscription_ids.length || r.subscription_ids.includes(c.sub_id)).map((c) => carLabel(c.plate, c.vehicle_type_name)).join(", ")}
                          {resident.cars[0]?.service_name ? ` · ${titleCase(resident.cars[0].service_name)}` : ""}
                        </span>
                      ) : null}
                    </span>
                    {resident && <Btn onClick={() => setResidentRule({ resident, rule: r })}>Edit</Btn>}
                  </li>
                );
              })}
            </ul>
          )}
          {residentsWithout.length > 0 ? (
            <select aria-label="Add a repeat wash for a resident" className="h-10 w-full rounded-[10px] border border-[#E4E9F1] bg-white px-3 text-sm text-[#0E1A33]" value=""
              onChange={(e) => { const r = s.residents.find((x) => x.enrollment_id === e.target.value); if (r) setResidentRule({ resident: r, rule: null }); }}>
              <option value="">+ Add A Repeat Wash For…</option>
              {residentsWithout.map((r) => <option key={r.enrollment_id} value={r.enrollment_id}>{r.flat} · {r.resident_name}{r.cars.length ? ` · ${r.cars.map((c) => carLabel(c.plate, c.vehicle_type_name)).join(", ")}` : ""}</option>)}
            </select>
          ) : !s.residents.length ? <p className="text-[13px] text-[#5F6878]">No Resident With A Live Plan Yet</p> : null}
        </Section>

        <Section title="Change Requests" description="Residents asking to skip or move one premium wash.">
          <RequestList requests={s.requests} slots={s.slots} onChanged={ok} />
        </Section>
      </div>

      <Section title="Premium Washes Left This Plan Month" description={atRisk.length ? `${atRisk.reduce((n, o) => n + o.at_risk, 0)} premium wash(es) have no date before their plan month ends.` : "Every remaining premium wash has a day."}>
        {!s.outlook.length ? <p className="text-[13px] font-semibold text-[#0E1A33]">No Live Plans</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-[13px]">
              <thead className="text-[12px] text-[#5F6878]">
                <tr><th className="py-1.5 font-semibold">Flat</th><th className="font-semibold">Car</th><th className="font-semibold">Left</th><th className="font-semibold">Planned</th><th className="font-semibold">No Date Yet</th><th className="font-semibold">Month Ends</th><th /></tr>
              </thead>
              <tbody className="divide-y divide-[#EEF2F7]">
                {s.outlook.map((o) => {
                  const resident = s.residents.find((x) => x.enrollment_id === o.enrollment_id);
                  return (
                    <tr key={o.sub_id} className="text-[#0E1A33]">
                      <td className="py-1.5">{o.flat} <span className="text-[#5F6878]">· {o.resident_name}</span></td>
                      <td className="text-[12px]"><b className="font-mono">{o.plate}</b>{o.vehicle_type_name ? ` · ${titleCase(o.vehicle_type_name)}` : ""}<span className="block text-[#5F6878]">{titleCase(o.service_name)}</span></td>
                      <td>{o.remaining}/{o.total}</td>
                      <td>{o.planned}{o.booked_upcoming ? <span className="text-[#5F6878]"> ({o.booked_upcoming} Booked)</span> : null}</td>
                      <td className={o.at_risk ? "font-bold text-[#B45309]" : "text-[#5F6878]"}>{o.at_risk || "—"}</td>
                      <td className="text-[#5F6878]">{formatShortDate(o.end_date)}</td>
                      <td className="text-right">
                        {resident && !s.resident_rules.some((r) => r.is_active && r.enrollment_id === o.enrollment_id) && o.at_risk > 0 && (
                          <button type="button" className="inline-flex items-center gap-1 text-[12px] font-semibold text-[#0A66F0]" onClick={() => setResidentRule({ resident, rule: null })}>
                            <UserRound className="h-3.5 w-3.5" /> Repeat Wash
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <SocietyRuleModal open={!!ruleModal} onClose={() => setRuleModal(null)} societyId={societyId} rule={ruleModal?.rule} slots={s.slots} captains={s.captains}
        defaultWashes={s.settings.default_washes_per_captain} onSaved={(m) => { setRuleModal(null); ok(m); }} />
      <AddVisitModal open={addVisit} onClose={() => setAddVisit(false)} societyId={societyId} slots={s.slots} captains={s.captains}
        defaultWashes={s.settings.default_washes_per_captain} onSaved={(m) => { setAddVisit(false); ok(m); }} />
      <ResidentRuleModal open={!!residentRule} onClose={() => setResidentRule(null)} societyId={societyId} resident={residentRule?.resident || null}
        rule={residentRule?.rule} slots={s.slots} captains={s.captains} onSaved={(m) => { setResidentRule(null); ok(m); }} />
    </div>
  );
}

/** "Repeat wash" on a resident row of the Residents tab. */
export function ResidentScheduleButton({ societyId, enrollmentId }: { societyId: string; enrollmentId: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["society-schedule", societyId], queryFn: () => scheduleApi.society(societyId), enabled: open });
  const resident = q.data?.residents.find((r) => r.enrollment_id === enrollmentId) || null;
  const rule = q.data?.resident_rules.find((r) => r.is_active && r.enrollment_id === enrollmentId) || null;
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex items-center gap-1.5 rounded-full border border-[#E4E9F1] px-3 py-1.5 text-sm font-medium text-[#0E1A33] hover:bg-[#EEF3FA]">
        <Repeat className="h-4 w-4" /> Repeat Wash
      </button>
      {open && q.isLoading && <span className="text-xs text-[#5F6878]">Loading…</span>}
      {open && q.isError && !q.data && (
        <span role="alert" className="text-xs text-[#5F6878]">
          Couldn't load the schedule.{" "}
          <button type="button" className="font-semibold text-[#0A66F0] disabled:opacity-60" disabled={q.isFetching} onClick={() => void q.refetch()}>
            Try Again
          </button>
        </span>
      )}
      {open && q.data && !resident && <span className="text-xs text-[#5F6878]">No Live Car On A Plan</span>}
      {q.data && (
        <ResidentRuleModal open={open && !!resident} onClose={() => setOpen(false)} societyId={societyId} resident={resident} rule={rule}
          slots={q.data.slots} captains={q.data.captains}
          onSaved={(m) => { setOpen(false); toast.push({ tone: "success", title: m }); queryClient.invalidateQueries({ queryKey: ["society-schedule", societyId] }); }} />
      )}
    </>
  );
}
