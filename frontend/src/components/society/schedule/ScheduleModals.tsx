/**
 * Society schedule editors: a society's repeat visit day, a one-off visit
 * day, a resident's own repeat wash, and answering resident change requests.
 */
import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Modal } from "../../ui";
import { getErrorMessage } from "../../../lib/api-client";
import { todayIST } from "../../../lib/date";
import type { Person } from "../../../api/society";
import {
  scheduleApi,
  type CarOutlook,
  type RulePattern,
  type ScheduleRequest,
  type ScheduleRule,
  type SlotOption,
} from "../../../api/societySchedule";
import { addDays, Banner, Btn, CaptainPicker, Field, FieldGroup, nextWeekday, PatternEditor, SlotPicker, ui } from "./scheduleUi";
import { carLabel } from "../../../api/society";
import { titleCase } from "../../public/landing/shared";

function patternOk(p: RulePattern): boolean {
  if (p.kind === "monthly_nth") return !!p.weeks?.length;
  if (p.kind === "every_n_weeks") return !!p.anchor_date;
  return true;
}

/** A society's repeat visit day ("every 2 weeks on Saturday, 8–2, two captains, 6 each"). */
export function SocietyRuleModal({ open, onClose, societyId, rule, slots, captains, defaultWashes, onSaved }: {
  open: boolean; onClose: () => void; societyId: string; rule?: ScheduleRule | null; slots: SlotOption[]; captains: Person[];
  defaultWashes: number; onSaved: (message: string) => void;
}) {
  const [pattern, setPattern] = useState<RulePattern>({ kind: "weekly", weekday: 5 });
  const [slotKeys, setSlotKeys] = useState<string[]>([]);
  const [captainIds, setCaptainIds] = useState<string[]>([]);
  const [washes, setWashes] = useState(defaultWashes);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setError("");
    setPattern(rule?.pattern || { kind: "every_n_weeks", weekday: 5, interval_weeks: 2, anchor_date: nextWeekday(5) });
    setSlotKeys(rule?.slot_keys || slots.slice(0, 2).map((s) => s.key));
    setCaptainIds(rule?.captains.map((c) => c.id) || []);
    setWashes(rule?.washes_per_captain || defaultWashes);
    setStartDate(rule?.start_date || "");
    setEndDate(rule?.end_date || "");
  }, [open, rule, slots, defaultWashes]);
  const save = useMutation({
    mutationFn: () => {
      const payload = { pattern, slot_keys: slotKeys, captain_ids: captainIds, washes_per_captain: washes, start_date: startDate || null, end_date: endDate || null };
      return rule ? scheduleApi.updateRule(rule.id, payload) : scheduleApi.createRule(societyId, payload);
    },
    onSuccess: () => onSaved(rule ? "Repeat saved — upcoming days re-planned" : "Repeat visit day saved"),
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <Modal open={open} onClose={onClose} title={rule ? "Edit Repeat Visit Day" : "Repeat Visit Day"} maxWidth="max-w-xl">
      <div className="space-y-4">
        <p className="text-[13px] text-[#5F6878]">When captains come to this society for premium washes. Each day books the residents who still have premium washes left, most urgent first.</p>
        <FieldGroup label="Repeat"><PatternEditor value={pattern} onChange={setPattern} /></FieldGroup>
        <FieldGroup label="Slots The Captains Work"><SlotPicker slots={slots} value={slotKeys} onChange={setSlotKeys} /></FieldGroup>
        <FieldGroup label="Captains" hint="Leave empty to assign from the booking queue later."><CaptainPicker captains={captains} value={captainIds} onChange={setCaptainIds} /></FieldGroup>
        <Field label="Premium Washes Per Captain That Day" hint={`Up to ${Math.max(1, captainIds.length) * washes} cars per visit day.`}>
          <input type="number" min={1} max={20} className={ui.input} value={washes} onChange={(e) => setWashes(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="From (Optional)"><input type="date" className={ui.input} min={addDays(todayIST(), 1)} value={startDate} onChange={(e) => setStartDate(e.target.value)} /></Field>
          <Field label="Until (Optional)"><input type="date" className={ui.input} min={addDays(todayIST(), 1)} value={endDate} onChange={(e) => setEndDate(e.target.value)} /></Field>
        </div>
        {rule && <Banner tone="warn">Saving re-plans the upcoming days of this repeat — single-day changes on days not booked yet are reset. Booked days stay.</Banner>}
        {error && <Banner tone="error">{error}</Banner>}
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn kind="primary" disabled={save.isPending || !slotKeys.length || !patternOk(pattern)} onClick={() => { setError(""); save.mutate(); }}>Save Repeat</Btn>
        </div>
      </div>
    </Modal>
  );
}

/** A one-off society visit day. */
export function AddVisitModal({ open, onClose, societyId, slots, captains, defaultWashes, onSaved, societies }: {
  open: boolean; onClose: () => void; societyId?: string; slots: SlotOption[]; captains: Person[]; defaultWashes: number;
  onSaved: (message: string) => void; societies?: { id: string; name: string }[];
}) {
  const [target, setTarget] = useState(societyId || "");
  const [date, setDate] = useState(nextWeekday(5));
  const [slotKeys, setSlotKeys] = useState<string[]>([]);
  const [captainIds, setCaptainIds] = useState<string[]>([]);
  const [washes, setWashes] = useState(defaultWashes);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setError("");
    setTarget(societyId || societies?.[0]?.id || "");
    setSlotKeys(slots.slice(0, 2).map((s) => s.key));
    setWashes(defaultWashes);
  }, [open, societyId, societies, slots, defaultWashes]);
  const save = useMutation({
    mutationFn: () => scheduleApi.createVisit(target, { date, slot_keys: slotKeys, captain_ids: captainIds, washes_per_captain: washes }),
    onSuccess: () => onSaved("Visit day added"),
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <Modal open={open} onClose={onClose} title="Add A Visit Day" maxWidth="max-w-xl">
      <div className="space-y-4">
        {societies && (
          <Field label="Society">
            <select className={ui.input} value={target} onChange={(e) => setTarget(e.target.value)}>
              {societies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
        )}
        <Field label="Date" hint="Tomorrow or later."><input type="date" className={ui.input} min={addDays(todayIST(), 1)} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <FieldGroup label="Slots The Captains Work"><SlotPicker slots={slots} value={slotKeys} onChange={setSlotKeys} /></FieldGroup>
        <FieldGroup label="Captains"><CaptainPicker captains={captains} value={captainIds} onChange={setCaptainIds} /></FieldGroup>
        <Field label="Premium Washes Per Captain" hint={`Up to ${Math.max(1, captainIds.length) * washes} cars that day.`}>
          <input type="number" min={1} max={20} className={ui.input} value={washes} onChange={(e) => setWashes(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn kind="primary" disabled={save.isPending || !slotKeys.length || !target || !date} onClick={() => { setError(""); save.mutate(); }}>Add Day</Btn>
        </div>
      </div>
    </Modal>
  );
}

/** One resident's own repeat premium wash ("every Saturday, 8–11"). */
export function ResidentRuleModal({ open, onClose, societyId, resident, rule, slots, captains, onSaved }: {
  open: boolean; onClose: () => void; societyId: string;
  resident: { enrollment_id: string; resident_name?: string | null; flat?: string | null; cars: CarOutlook[] } | null;
  rule?: ScheduleRule | null; slots: SlotOption[]; captains: Person[]; onSaved: (message: string) => void;
}) {
  const [pattern, setPattern] = useState<RulePattern>({ kind: "weekly", weekday: 5 });
  const [slotKey, setSlotKey] = useState<string[]>([]);
  const [captainIds, setCaptainIds] = useState<string[]>([]);
  const [subs, setSubs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(false);
  useEffect(() => {
    if (!open || !resident) return;
    setError("");
    setPattern(rule?.pattern || { kind: "weekly", weekday: 5 });
    setSlotKey(rule?.slot_keys?.length ? [rule.slot_keys[0]] : slots[0] ? [slots[0].key] : []);
    setCaptainIds(rule?.captains.map((c) => c.id) || []);
    setSubs(rule?.subscription_ids?.length ? rule.subscription_ids : resident.cars.map((c) => c.sub_id));
  }, [open, rule, resident, slots]);
  const save = useMutation({
    mutationFn: () => {
      const payload = { enrollment_id: resident!.enrollment_id, subscription_ids: subs, pattern, slot_key: slotKey[0], captain_id: captainIds[0] || null };
      return rule ? scheduleApi.updateRule(rule.id, payload) : scheduleApi.createResidentRule(societyId, payload);
    },
    onSuccess: () => onSaved("Repeat wash saved"),
    onError: (err) => setError(getErrorMessage(err)),
  });
  if (!resident) return null;
  const quota = resident.cars[0];
  return (
    <Modal open={open} onClose={onClose} title={`Repeat Wash — ${resident.resident_name || "Resident"} (${resident.flat || "—"})`} maxWidth="max-w-xl">
      <div className="space-y-4">
        <p className="text-[13px] text-[#5F6878]">
          Their premium washes on a fixed day, booked automatically a couple of days ahead. {quota ? `${quota.total} × ${titleCase(quota.service_name) || "Premium Wash"} a month on this plan — extra days beyond that are skipped automatically.` : ""}
        </p>
        <FieldGroup label="Cars">
          <div className="flex flex-wrap gap-1.5">
            {resident.cars.map((c) => {
              const on = subs.includes(c.sub_id);
              return (
                <button key={c.sub_id} type="button" onClick={() => setSubs(on ? subs.filter((s) => s !== c.sub_id) : [...subs, c.sub_id])}
                  className={`rounded-full border px-3 py-1 text-[13px] font-semibold ${on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] text-[#0E1A33]"}`}>
                  <span className="font-mono">{c.plate}</span>{c.vehicle_type_name ? ` · ${titleCase(c.vehicle_type_name)}` : ""}
                </button>
              );
            })}
          </div>
        </FieldGroup>
        <FieldGroup label="Repeat"><PatternEditor value={pattern} onChange={setPattern} /></FieldGroup>
        <FieldGroup label="Slot"><SlotPicker slots={slots} value={slotKey} onChange={setSlotKey} single /></FieldGroup>
        <FieldGroup label="Captain" hint="Empty = the society's daily captain."><CaptainPicker captains={captains} value={captainIds} onChange={setCaptainIds} single /></FieldGroup>
        {error && <Banner tone="error">{error}</Banner>}
        <div className="flex flex-wrap justify-between gap-2">
          <div>
            {rule && (
              <Btn kind="danger" disabled={removing} onClick={async () => {
                setRemoving(true);
                try { await scheduleApi.deleteRule(rule.id); onSaved("Repeat wash removed — booked days stay"); } catch (err) { setError(getErrorMessage(err)); } finally { setRemoving(false); }
              }}>Remove Repeat</Btn>
            )}
          </div>
          <div className="flex gap-2">
            <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
            <Btn kind="primary" disabled={save.isPending || !subs.length || !slotKey.length || !patternOk(pattern)} onClick={() => { setError(""); save.mutate(); }}>Save Repeat Wash</Btn>
          </div>
        </div>
      </div>
    </Modal>
  );
}

/** Pending resident requests: approve (optionally another date/slot) or decline. */
export function RequestList({ requests, slots, onChanged }: { requests: ScheduleRequest[]; slots: SlotOption[]; onChanged: (message: string) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const answer = async (r: ScheduleRequest, approve: boolean) => {
    setBusy(r.id);
    setError("");
    try {
      if (approve) await scheduleApi.approve(r.id, {});
      else await scheduleApi.decline(r.id);
      onChanged(approve ? "Approved — the resident is told" : "Declined — the resident is told");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };
  if (!requests.length) return <p className="text-[13px] font-semibold text-[#0E1A33]">No Requests Waiting</p>;
  return (
    <div className="space-y-2">
      {error && <Banner tone="error">{error}</Banner>}
      {requests.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[12px] bg-[#F7F9FC] px-3 py-2.5 text-[13px]" data-testid="schedule-request">
          <span className="min-w-0">
            <b className="text-[#0E1A33]">{r.resident_name} · {r.flat}</b>{" "}
            <span className="text-[12px]">{(r.cars?.length ? r.cars.map((c) => carLabel(c.plate, c.vehicle_type_name)) : r.plates).join(", ")}</span>
            <span className="block text-[#5F6878]">
              {r.kind === "skip" ? "Skip" : "Move"} {r.visit_date_label}{r.slot_label ? ` (${r.slot_label})` : ""}
              {r.kind === "move" && <> → <b className="text-[#0E1A33]">{r.preferred_date_label}{r.preferred_slot_label ? `, ${r.preferred_slot_label}` : ""}</b></>}
              {r.note ? ` — “${r.note}”` : ""}
            </span>
          </span>
          <span className="flex gap-2">
            <Btn kind="primary" disabled={busy === r.id} onClick={() => answer(r, true)}>Approve</Btn>
            <Btn kind="ghost" disabled={busy === r.id} onClick={() => answer(r, false)}>Decline</Btn>
          </span>
        </div>
      ))}
      {slots.length === 0 && null}
    </div>
  );
}
