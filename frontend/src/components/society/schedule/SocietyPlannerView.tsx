/**
 * Center-level society planner (desktop-first): a 4-week calendar of every
 * society's premium-wash visit days, the rotation builder ("4 societies over
 * Saturdays and Sundays"), conflicts, and which societies still have premium
 * washes without a date. Manager = own center; admin picks a center.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, CalendarPlus, ChevronLeft, ChevronRight, Shuffle } from "lucide-react";
import { Modal, Spinner } from "../../ui";
import { adminServiceCenterApi } from "../../../api/admin";
import { useToast } from "../../../context/ToastContext";
import { getErrorMessage } from "../../../lib/api-client";
import { todayIST } from "../../../lib/date";
import { cn } from "../../../lib/cn";
import { scheduleApi, WEEKDAYS_SHORT, type Planner, type RotationPreview, type SocietyVisit } from "../../../api/societySchedule";
import { AddVisitModal } from "./ScheduleModals";
import { VisitDayCard } from "./VisitDayCard";
import { addDays, Banner, Btn, CaptainPicker, Field, FieldGroup, nextWeekday, SlotPicker, Toggle, ui } from "./scheduleUi";
import { titleCase } from "../../public/landing/shared";

function mondayOf(day: string): string {
  const d = new Date(`${day}T12:00:00+05:30`);
  const back = (d.getUTCDay() + 6) % 7;
  return addDays(day, -back);
}

const CHIP: Record<string, string> = {
  planned: "border-[#D6E4FD] bg-white text-[#0E1A33]",
  booked: "border-[#0A66F0] bg-[#E8F0FE] text-[#0A3E91]",
  done: "border-green-200 bg-green-50 text-green-800",
  partial: "border-amber-300 bg-amber-50 text-amber-900",
  failed: "border-red-200 bg-red-50 text-[#B91C1C]",
  missed: "border-red-200 bg-red-50 text-[#B91C1C]",
  empty: "border-[#E4E9F1] bg-[#F7F9FC] text-[#5F6878]",
  skipped: "border-[#E4E9F1] bg-[#F7F9FC] text-[#A3AAB6] line-through",
  generating: "border-[#0A66F0] bg-[#E8F0FE] text-[#0A3E91]",
};

export function SocietyPlannerView({ isAdmin = false, societyBasePath }: { isAdmin?: boolean; societyBasePath: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [centerId, setCenterId] = useState("");
  const [start, setStart] = useState(() => mondayOf(todayIST()));
  const [picked, setPicked] = useState<string | null>(null);
  const [rotation, setRotation] = useState(false);
  const [addVisit, setAddVisit] = useState(false);
  const end = addDays(start, 27);
  const centers = useQuery({ queryKey: ["admin-centers-all"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }), enabled: isAdmin });
  const effectiveCenter = isAdmin ? centerId || centers.data?.data?.[0]?.id || "" : "";
  const q = useQuery({
    queryKey: ["society-planner", effectiveCenter, start],
    queryFn: () => scheduleApi.planner({ center_id: effectiveCenter || undefined, start, end }),
    enabled: !isAdmin || !!effectiveCenter,
  });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["society-planner"] });
    queryClient.invalidateQueries({ queryKey: ["society-schedule"] });
  };
  const p = q.data;
  const byDate = useMemo(() => {
    const out: Record<string, SocietyVisit[]> = {};
    for (const v of p?.visits || []) (out[v.date] ||= []).push(v);
    return out;
  }, [p]);
  const visit = p?.visits.find((v) => v.id === picked) || null;
  const days = Array.from({ length: 28 }, (_, i) => addDays(start, i));
  const today = todayIST();

  return (
    <div className="space-y-4" data-testid="society-planner">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-[#0E1A33]">Society Planner</h1>
          <p className="text-sm text-[#5F6878]">Premium-wash visit days for every society{p ? ` of ${p.center.name}` : ""} — booked automatically {p?.settings.generate_days_ahead ?? 2} days ahead.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin && (
            <select aria-label="Service center" className="h-9 rounded-[10px] border border-[#E4E9F1] bg-white px-3 text-sm" value={effectiveCenter} onChange={(e) => setCenterId(e.target.value)}>
              {(centers.data?.data || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          <Btn onClick={() => setAddVisit(true)} disabled={!p?.societies.length}><CalendarPlus className="h-4 w-4" /> Add Visit Day</Btn>
          <Btn kind="cta" onClick={() => setRotation(true)} disabled={!p?.societies.length}><Shuffle className="h-4 w-4" /> Build Rotation</Btn>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button type="button" aria-label="Previous weeks" className="rounded-full p-2 hover:bg-[#EEF3FA]" onClick={() => setStart((s) => addDays(s, -14))}><ChevronLeft className="h-4 w-4" /></button>
        <span className="min-w-[180px] text-center text-sm font-semibold text-[#0E1A33]">
          {new Date(`${start}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} – {new Date(`${end}T12:00:00+05:30`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}
        </span>
        <button type="button" aria-label="Next weeks" className="rounded-full p-2 hover:bg-[#EEF3FA]" onClick={() => setStart((s) => addDays(s, 14))}><ChevronRight className="h-4 w-4" /></button>
        <Btn kind="ghost" onClick={() => setStart(mondayOf(todayIST()))}>This Week</Btn>
      </div>

      {q.isLoading || (isAdmin && centers.isLoading) ? <div className="flex justify-center py-16"><Spinner /></div> : !p ? (
        <Banner tone="error">{getErrorMessage(q.error) || "Pick A Service Center"}</Banner>
      ) : (
        <>
          {p.warnings.length > 0 && <div className="space-y-1.5">{p.warnings.slice(0, 6).map((w, i) => <Banner key={i} tone="warn">{w.message}</Banner>)}</div>}

          <div className={`${ui.card} overflow-x-auto`}>
            <div className="grid min-w-[900px] grid-cols-7 border-b border-[#E4E9F1] text-[12px] font-semibold text-[#5F6878]">
              {WEEKDAYS_SHORT.map((d, i) => <div key={d} className={cn("px-2 py-2", i >= 5 && "bg-[#F7F9FC] text-[#0E1A33]")}>{d}</div>)}
            </div>
            <div className="grid min-w-[900px] grid-cols-7">
              {days.map((d, i) => {
                const list = byDate[d] || [];
                const weekend = i % 7 >= 5;
                return (
                  <div key={d} className={cn("min-h-[104px] border-b border-r border-[#EEF2F7] p-1.5", weekend && "bg-[#F7F9FC]", d < today && "opacity-60")} data-testid="planner-day">
                    <p className={cn("mb-1 text-[12px] font-bold", d === today ? "text-[#0A66F0]" : "text-[#0E1A33]")}>
                      {Number(d.slice(8))}{d.slice(8) === "01" || i === 0 ? ` ${new Date(`${d}T12:00:00+05:30`).toLocaleDateString("en-IN", { month: "short" })}` : ""}
                    </p>
                    <div className="space-y-1">
                      {list.map((v) => (
                        <button key={v.id} type="button" onClick={() => setPicked(v.id)} data-testid="planner-chip"
                          className={cn("block w-full truncate rounded-[8px] border px-1.5 py-1 text-left text-[11px] font-semibold", CHIP[v.display_status] || CHIP.planned)}>
                          {v.kind === "resident" ? `${v.society_name?.split(" ")[0]} · ${v.allocations[0]?.flat || v.skipped[0]?.flat || "Resident"}` : v.society_name}
                          <span className="block font-normal opacity-80">
                            {v.kind === "society" ? `${v.allocated_cars}/${v.capacity ?? "—"} Cars · ${v.captains.length || 0} Capt.`
                              : v.allocations.length || v.status !== "planned" ? `Repeat Wash · ${v.window_label}` : "Repeat · No Wash Left"}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
          <p className="flex flex-wrap gap-3 text-[12px] text-[#5F6878]">
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm border border-[#D6E4FD] bg-white align-middle" />Planned (Cars/Capacity)</span>
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-[#E8F0FE] align-middle" />Booked</span>
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-amber-50 align-middle ring-1 ring-amber-300" />Partly Booked</span>
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-[#F7F9FC] align-middle ring-1 ring-[#E4E9F1]" />Skipped / No Cars Due</span>
          </p>

          <section className={`${ui.card} p-4`}>
            <h2 className="mb-2 text-[15px] font-bold text-[#0E1A33]">Societies</h2>
            {!p.societies.length ? <p className="text-sm text-[#5F6878]">No Societies In This Center Yet</p> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-[13px]">
                  <thead className="text-[12px] text-[#5F6878]">
                    <tr><th className="py-1.5 font-semibold">Society</th><th className="font-semibold">Repeat</th><th className="font-semibold">Next Visit</th><th className="font-semibold">Cars</th><th className="font-semibold">Premium Left</th><th className="font-semibold">No Date Yet</th></tr>
                  </thead>
                  <tbody className="divide-y divide-[#EEF2F7]">
                    {p.societies.map((s) => (
                      <tr key={s.id} className="text-[#0E1A33]">
                        <td className="py-2"><Link to={`${societyBasePath}/${s.id}?tab=schedule`} className="font-semibold text-[#0A66F0] hover:underline">{s.name}</Link>{s.area ? <span className="text-[#5F6878]"> · {s.area}</span> : null}</td>
                        <td className="text-[#5F6878]">{s.rules.filter((r) => r.is_active).map((r) => titleCase(r.label)).join("; ") || "—"}</td>
                        <td>{s.next_visit ? <button type="button" className="font-semibold hover:underline" onClick={() => setPicked(s.next_visit!.id)}>{s.next_visit.date_label}</button> : "—"}</td>
                        <td>{s.active_cars}</td>
                        <td>{s.premium_left}</td>
                        <td className={s.at_risk ? "font-bold text-[#B45309]" : "text-[#5F6878]"}>{s.at_risk || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <Modal open={!!visit} onClose={() => setPicked(null)} title={visit ? `${visit.society_name} · ${visit.date_label}` : ""} maxWidth="max-w-2xl">
            {visit && <VisitDayCard visit={visit} slots={p.slots} captains={p.captains} onChanged={() => { refresh(); }} showSociety defaultOpen />}
          </Modal>
          <RotationModal open={rotation} onClose={() => setRotation(false)} planner={p} centerId={isAdmin ? effectiveCenter : undefined}
            onSaved={(n) => { setRotation(false); toast.push({ tone: "success", title: `Rotation saved for ${n} societ${n === 1 ? "y" : "ies"}` }); refresh(); }} />
          <AddVisitModal open={addVisit} onClose={() => setAddVisit(false)} slots={p.slots} captains={p.captains} defaultWashes={p.settings.default_washes_per_captain}
            societies={p.societies.map((s) => ({ id: s.id, name: s.name }))} onSaved={(m) => { setAddVisit(false); toast.push({ tone: "success", title: m }); refresh(); }} />
        </>
      )}
    </div>
  );
}

function RotationModal({ open, onClose, planner, centerId, onSaved }: { open: boolean; onClose: () => void; planner: Planner; centerId?: string; onSaved: (n: number) => void }) {
  const [order, setOrder] = useState<string[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [weekdays, setWeekdays] = useState<number[]>([5, 6]);
  const [startDate, setStartDate] = useState(nextWeekday(5));
  const [slotKeys, setSlotKeys] = useState<string[]>([]);
  const [captainIds, setCaptainIds] = useState<string[]>([]);
  const [washes, setWashes] = useState(planner.settings.default_washes_per_captain);
  const [preview, setPreview] = useState<RotationPreview[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setOrder(planner.societies.map((s) => s.id));
    setPicked(new Set(planner.societies.map((s) => s.id)));
    setSlotKeys(planner.slots.slice(0, 2).map((s) => s.key));
    setWashes(planner.settings.default_washes_per_captain);
    setPreview(null);
    setError("");
    // Reset only when the dialog opens, not on every planner refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const chosen = order.filter((id) => picked.has(id));
  const payload = { center_id: centerId, society_ids: chosen, weekdays, start_date: startDate, slot_keys: slotKeys, captain_ids: captainIds, washes_per_captain: washes };
  const run = useMutation({
    mutationFn: (dry: boolean) => scheduleApi.rotation({ ...payload, dry_run: dry }),
    onSuccess: (r, dry) => { if (dry) setPreview(r.preview); else onSaved(r.created); },
    onError: (err) => setError(getErrorMessage(err)),
  });
  const name = (id: string) => planner.societies.find((s) => s.id === id)?.name || "—";
  const move = (i: number, by: number) => {
    const next = [...order];
    const j = i + by;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
    setPreview(null);
  };
  return (
    <Modal open={open} onClose={onClose} title="Build A Rotation" maxWidth="max-w-2xl">
      <div className="space-y-4">
        <p className="text-[13px] text-[#5F6878]">One society per chosen day, in this order, repeating every round — e.g. 4 societies over Saturday + Sunday: A and B in week 1, C and D in week 2, then again. Replaces each picked society's repeat visit days (booked days stay).</p>
        <FieldGroup label="Societies (Order = Visit Order)">
          <ul className="divide-y divide-[#EEF2F7] rounded-[12px] border border-[#E4E9F1]">
            {order.map((id, i) => (
              <li key={id} className="flex items-center gap-2 px-3 py-1.5 text-[13px]">
                <input type="checkbox" checked={picked.has(id)} onChange={() => { const n = new Set(picked); if (n.has(id)) n.delete(id); else n.add(id); setPicked(n); setPreview(null); }} aria-label={`Include ${name(id)}`} />
                <span className="flex-1 font-semibold text-[#0E1A33]">{name(id)}</span>
                <button type="button" aria-label="Move up" className="rounded p-1 hover:bg-[#EEF3FA]" onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /></button>
                <button type="button" aria-label="Move down" className="rounded p-1 hover:bg-[#EEF3FA]" onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /></button>
              </li>
            ))}
          </ul>
        </FieldGroup>
        <FieldGroup label="Days">
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS_SHORT.map((d, i) => <Toggle key={d} on={weekdays.includes(i)} onClick={() => { setWeekdays(weekdays.includes(i) ? weekdays.filter((x) => x !== i) : [...weekdays, i].sort()); setPreview(null); }}>{d}</Toggle>)}
          </div>
        </FieldGroup>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="First Day"><input type="date" className={ui.input} min={addDays(todayIST(), 1)} value={startDate} onChange={(e) => { setStartDate(e.target.value); setPreview(null); }} /></Field>
          <Field label="Premium Washes Per Captain"><input type="number" min={1} max={20} className={ui.input} value={washes} onChange={(e) => setWashes(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} /></Field>
        </div>
        <FieldGroup label="Slots The Captains Work"><SlotPicker slots={planner.slots} value={slotKeys} onChange={setSlotKeys} /></FieldGroup>
        <FieldGroup label="Captains Sent" hint={`Up to ${Math.max(1, captainIds.length) * washes} cars per visit day.`}><CaptainPicker captains={planner.captains} value={captainIds} onChange={setCaptainIds} /></FieldGroup>
        {preview && (
          <div className="rounded-[12px] bg-[#F7F9FC] p-3 text-[13px]" data-testid="rotation-preview">
            <p className="mb-1.5 font-semibold text-[#0E1A33]">First Round</p>
            <ul className="space-y-1">
              {preview.map((r) => <li key={r.society_id}><b>{r.first_labels[0]}</b> → {r.society_name} <span className="text-[#5F6878]">({titleCase(r.label)})</span></li>)}
            </ul>
          </div>
        )}
        {error && <Banner tone="error">{error}</Banner>}
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn disabled={run.isPending || !chosen.length || !weekdays.length || !slotKeys.length} onClick={() => { setError(""); run.mutate(true); }}>Preview</Btn>
          <Btn kind="primary" disabled={run.isPending || !chosen.length || !weekdays.length || !slotKeys.length || !preview} onClick={() => { setError(""); run.mutate(false); }}>Save Rotation</Btn>
        </div>
      </div>
    </Modal>
  );
}
