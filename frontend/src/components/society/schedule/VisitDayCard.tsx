/**
 * One visit day (a society's premium-wash day, or one resident's repeat
 * wash on one date) with its cars and the single-day controls: change
 * (move / captains / slots / washes per captain), skip / restore, take a
 * car off this day, book now. Shared by the society Schedule tab and the
 * center planner.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronUp, Repeat, UserRound, X } from "lucide-react";
import { Modal } from "../../ui";
import { useToast } from "../../../context/ToastContext";
import { useConfirm } from "../../../context/ConfirmContext";
import { getErrorMessage } from "../../../lib/api-client";
import { formatDay, todayIST } from "../../../lib/date";
import type { Person } from "../../../api/society";
import { scheduleApi, type SlotOption, type SocietyVisit, type VisitChange } from "../../../api/societySchedule";
import { addDays, Banner, Btn, CaptainPicker, Field, FieldGroup, SlotPicker, StatusChip, ui } from "./scheduleUi";
import { carLabel } from "../../../api/society";
import { titleCase } from "../../public/landing/shared";

const BOOKING_STATUS: Record<string, string> = {
  pending: "Needs Captain", assigned: "Captain Assigned", captain_on_the_way: "On The Way", service_started: "Washing",
  completed: "Done", cancelled: "Cancelled", rescheduled: "Rescheduled", awaiting_payment: "Awaiting Payment",
};

export function VisitDayCard({ visit, slots, captains, onChanged, showSociety = false, defaultOpen = false, maxAdvanceDays = 7 }: {
  visit: SocietyVisit; slots: SlotOption[]; captains: Person[]; onChanged: () => void; showSociety?: boolean; defaultOpen?: boolean; maxAdvanceDays?: number;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [open, setOpen] = useState(defaultOpen);
  const [editing, setEditing] = useState(false);
  const done = (title: string, warnings?: string[]) => {
    toast.push({ tone: warnings?.length ? "warning" : "success", title, message: warnings?.join(" ") || undefined });
    onChanged();
  };
  const fail = (err: unknown) => toast.push({ tone: "error", title: "That didn't work", message: getErrorMessage(err) });
  const act = useMutation({
    mutationFn: async (kind: "skip" | "restore" | "book" | "remove") => {
      if (kind === "skip") return scheduleApi.skip(visit.id).then((r) => ({ title: "Day skipped", warnings: r.warnings }));
      if (kind === "restore") return scheduleApi.restore(visit.id).then((r) => ({ title: "Day restored", warnings: r.warnings }));
      if (kind === "remove") return scheduleApi.remove(visit.id).then(() => ({ title: "Visit day removed", warnings: [] as string[] }));
      return scheduleApi.bookNow(visit.id).then((r) => ({ title: `${r.booked} premium wash${r.booked === 1 ? "" : "es"} booked`, warnings: r.error ? [r.error] : [] }));
    },
    onSuccess: (r) => done(r.title, r.warnings),
    onError: fail,
  });
  const exclude = useMutation({
    mutationFn: ({ subs, excluded }: { subs: string[]; excluded: boolean }) => scheduleApi.exclude(visit.id, subs, excluded),
    onSuccess: (r, v) => done(v.excluded ? "Taken off this day" : "Put back on this day", r.warnings),
    onError: fail,
  });

  const today = todayIST();
  const isResident = visit.kind === "resident";
  const generated = ["booked", "partial", "failed", "empty"].includes(visit.status);
  const canBookNow = visit.editable && (visit.status === "planned" || visit.status === "partial" || visit.status === "failed" || visit.status === "empty")
    && visit.date > today && visit.date <= addDays(today, maxAdvanceDays - 1);
  const captainNames = visit.captains.map((c) => c.name).filter(Boolean).join(", ") || (visit.fallback_captain ? `${visit.fallback_captain.name} (Daily Captain)` : "No Captain Yet");
  const who = isResident ? visit.allocations[0]?.resident_name || visit.skipped[0]?.resident_name : null;
  const excludedRows = visit.excluded_subscription_ids;

  return (
    <article className={`${ui.card} p-3.5`} data-testid="visit-day">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-[15px] font-bold text-[#0E1A33]">
            {visit.date_label}
            <StatusChip visit={visit} />
            {visit.rule_id ? <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#5F6878]"><Repeat className="h-3 w-3" /> Repeat</span>
              : <span className="text-[11px] font-semibold text-[#5F6878]">One-Off</span>}
            {visit.moved_from && <span className="text-[11px] font-semibold text-[#5F6878]">Moved From {formatDay(visit.moved_from)}</span>}
          </p>
          <p className="mt-0.5 text-[13px] text-[#5F6878]">
            {showSociety && <><b className="text-[#0E1A33]">{visit.society_name}</b> · </>}
            {isResident && <><UserRound className="mb-0.5 inline h-3.5 w-3.5" /> {who || "Resident"}'s Repeat Wash · </>}
            {visit.window_label} · {captainNames}
            {!isResident && <> · {visit.washes_per_captain} Washes Each</>}
          </p>
          {visit.status === "planned" && visit.generate_on && (
            <p className="mt-0.5 flex items-center gap-1 text-[12px] text-[#5F6878]"><CalendarClock className="h-3.5 w-3.5" /> Books automatically on {formatDay(visit.generate_on)} — residents get their confirmation then.</p>
          )}
          {visit.generation_error && generated && <p className="mt-0.5 text-[12px] text-[#B91C1C]">{visit.generation_error}</p>}
        </div>
        <div className="text-right">
          <p className="font-mono-num text-[20px] font-bold leading-none text-[#0E1A33]">
            {visit.allocated_cars}{!isResident && visit.capacity ? <span className="text-[13px] font-semibold text-[#5F6878]">/{visit.capacity}</span> : null}
          </p>
          <p className="text-[11px] text-[#5F6878]">{generated ? "Booked" : "Cars Planned"}</p>
        </div>
      </div>

      {(visit.allocations.length > 0 || visit.overflow.length > 0 || excludedRows.length > 0 || visit.skipped.length > 0) && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="mt-2 inline-flex items-center gap-1 text-[13px] font-semibold text-[#0A66F0]">
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />} {open ? "Hide Cars" : "Show Cars"}
        </button>
      )}

      {open && (
        <div className="mt-2 space-y-2">
          {visit.allocations.length > 0 && (
            <ul className="divide-y divide-[#EEF2F7] rounded-[12px] border border-[#EEF2F7]">
              {visit.allocations.map((a, i) => (
                <li key={`${a.enrollment_id}-${i}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[13px]">
                  <span className="min-w-0">
                    <b className="text-[#0E1A33]">{a.start_label || a.slot_label || "—"}</b>
                    <span className="text-[#5F6878]"> · {a.flat} · {a.resident_name}</span>
                    <span className="block text-[12px] text-[#0E1A33]">
                      {a.cars.map((c, j) => <span key={c.sub_id || j}>{j > 0 && ", "}<b className="font-mono">{c.plate || "—"}</b>{c.vehicle_type_name || c.service_name ? <span className="text-[#5F6878]"> · {[titleCase(c.vehicle_type_name), titleCase(c.service_name)].filter(Boolean).join(" · ")}</span> : null}</span>)}
                    </span>
                    {(a.warning || a.error) && <span className="block text-[12px] text-[#B45309]">{a.error || a.warning}</span>}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-right text-[12px] text-[#5F6878]">
                      {a.captain_name || "No Captain"}
                      <span className="block">
                        {a.status === "projected" ? "Planned" : a.status === "booked"
                          ? (a.booking_statuses.filter(Boolean).map((s) => BOOKING_STATUS[s as string] || titleCase(s as string)).join(", ") || "Booked")
                          : a.status === "failed" ? "Not Booked" : a.status === "cancelled" ? "Taken Off" : titleCase(a.status)}
                      </span>
                    </span>
                    {visit.editable && !isResident && a.status !== "cancelled" && a.status !== "failed" && (
                      <button type="button" aria-label="Take off this day" title="Not this day"
                        className="rounded-full p-1 text-[#5F6878] hover:bg-[#EEF3FA] hover:text-[#B91C1C]"
                        disabled={exclude.isPending}
                        onClick={async () => {
                          if (await confirm({ title: `Take ${a.flat} Off ${visit.date_label}?`, message: generated ? "Their booking for this day is cancelled and the premium wash goes back to their plan." : "They'll be planned on a later visit day.", confirmLabel: "Take Off" })) {
                            exclude.mutate({ subs: a.cars.map((c) => c.sub_id), excluded: true });
                          }
                        }}>
                        <X className="h-4 w-4" />
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {visit.overflow.length > 0 && (
            <Banner tone="warn">
              <b>{visit.overflow.reduce((n, u) => n + u.cars.length, 0)} due but not fitting:</b>{" "}
              {visit.overflow.map((u) => `${u.flat || "—"} (${u.cars.map((c) => carLabel(c.plate, c.vehicle_type_name)).join(", ")})`).join(" · ")} — {visit.overflow[0].reason}. Add a captain or washes per captain.
            </Banner>
          )}
          {excludedRows.length > 0 && (
            <p className="text-[12px] text-[#5F6878]">
              {excludedRows.length} car{excludedRows.length === 1 ? "" : "s"} taken off this day.{" "}
              {visit.editable && <button type="button" className="font-semibold text-[#0A66F0] underline" onClick={() => exclude.mutate({ subs: excludedRows, excluded: false })}>Put Back</button>}
            </p>
          )}
          {isResident && visit.skipped.length > 0 && visit.allocations.length === 0 && (
            <p className="text-[12px] text-[#5F6878]">{visit.skipped.map((s) => `${s.plate ? carLabel(s.plate, s.vehicle_type_name) : "Car"}: ${s.reason}`).join(" · ")}</p>
          )}
        </div>
      )}

      {visit.editable && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-[#EEF2F7] pt-3">
          {visit.status !== "skipped" && <Btn onClick={() => setEditing(true)}>Change This Day</Btn>}
          {visit.status === "skipped" ? (
            <Btn kind="secondary" disabled={act.isPending} onClick={() => act.mutate("restore")}>Restore</Btn>
          ) : (
            <Btn kind="ghost" disabled={act.isPending} onClick={async () => {
              if (await confirm({ title: `Skip ${visit.date_label}?`, message: generated ? "Every booking made for this day is cancelled and the premium washes go back to the plans." : "Only this day — the repeat stays.", confirmLabel: "Skip Day", tone: "danger", cancelLabel: "Keep" })) act.mutate("skip");
            }}>Skip</Btn>
          )}
          {canBookNow && (
            <Btn kind="secondary" disabled={act.isPending} onClick={() => act.mutate("book")}>{generated ? "Retry Booking" : "Book Now"}</Btn>
          )}
          {!visit.rule_id && ["planned", "skipped", "empty", "failed"].includes(visit.status) && (
            <Btn kind="danger" disabled={act.isPending} onClick={async () => { if (await confirm({ title: "Remove This Visit Day?", confirmLabel: "Remove", tone: "danger" })) act.mutate("remove"); }}>Remove</Btn>
          )}
        </div>
      )}
      <VisitEditModal open={editing} onClose={() => setEditing(false)} visit={visit} slots={slots} captains={captains}
        onSaved={(warnings) => { setEditing(false); done("Day changed", warnings); }} />
    </article>
  );
}

export function VisitEditModal({ open, onClose, visit, slots, captains, onSaved }: {
  open: boolean; onClose: () => void; visit: SocietyVisit; slots: SlotOption[]; captains: Person[]; onSaved: (warnings: string[]) => void;
}) {
  const isResident = visit.kind === "resident";
  const [date, setDate] = useState(visit.date);
  const [slotKeys, setSlotKeys] = useState<string[]>(visit.slot_keys);
  const [captainIds, setCaptainIds] = useState<string[]>(visit.captains.map((c) => c.id));
  const [washes, setWashes] = useState<number>(visit.washes_per_captain || 6);
  const [notes, setNotes] = useState(visit.notes || "");
  const [error, setError] = useState("");
  const generated = ["booked", "partial", "failed", "empty"].includes(visit.status);
  const save = useMutation({
    mutationFn: () => {
      const change: VisitChange = {};
      if (date !== visit.date) change.date = date;
      if (slotKeys.join() !== visit.slot_keys.join()) change.slot_keys = slotKeys;
      if (captainIds.join() !== visit.captains.map((c) => c.id).join()) change.captain_ids = captainIds;
      if (!isResident && washes !== visit.washes_per_captain) change.washes_per_captain = washes;
      if ((notes || "") !== (visit.notes || "")) change.notes = notes || null;
      return scheduleApi.updateVisit(visit.id, change);
    },
    onSuccess: (r) => onSaved(r.warnings),
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <Modal open={open} onClose={onClose} title={`Change ${visit.date_label} Only`}>
      <div className="space-y-4">
        {generated && <Banner tone="warn">Already booked. Moving the day cancels its bookings and books them again for the new date; new captains take over the bookings.</Banner>}
        <Field label="Date" hint="Tomorrow or later — residents get a day's notice.">
          <input type="date" className={ui.input} value={date} min={addDays(todayIST(), 1)} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <FieldGroup label={isResident ? "Slot" : "Slots The Captains Work"}>
          <SlotPicker slots={slots} value={slotKeys} onChange={setSlotKeys} single={isResident} />
        </FieldGroup>
        <FieldGroup label={isResident ? "Captain" : "Captains"}>
          <CaptainPicker captains={captains} value={captainIds} onChange={setCaptainIds} single={isResident} />
        </FieldGroup>
        {!isResident && (
          <Field label="Premium Washes Per Captain That Day">
            <input type="number" min={1} max={20} className={ui.input} value={washes} onChange={(e) => setWashes(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} />
          </Field>
        )}
        <Field label="Note (Optional)">
          <input className={ui.input} value={notes} maxLength={300} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. gate pass at the main gate" />
        </Field>
        {error && <Banner tone="error">{error}</Banner>}
        <div className="flex justify-end gap-2">
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn kind="primary" disabled={save.isPending || !slotKeys.length} onClick={() => { setError(""); save.mutate(); }}>Save This Day</Btn>
        </div>
      </div>
    </Modal>
  );
}
