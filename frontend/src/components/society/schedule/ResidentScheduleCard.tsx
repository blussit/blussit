/**
 * Resident: "Next premium wash: Sat 11 Oct, 9:00 AM – 12:00 PM", the
 * upcoming scheduled premium washes, and "Request change" (skip / another
 * day) that the society manager approves. Mobile-first; also exports the
 * one-line NextPremiumWash for the customer app's plan card.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Clock } from "lucide-react";
import { getErrorMessage } from "../../../lib/api-client";
import { todayIST } from "../../../lib/date";
import { scheduleApi, type MyScheduleItem } from "../../../api/societySchedule";
import { addDays } from "./scheduleUi";
import { carLabel } from "../../../api/society";

const carsLine = (cars: MyScheduleItem["cars"]) => cars.map((c) => carLabel(c.plate, c.vehicle_type_name)).join(", ");

const card = "rounded-[18px] border border-[#E4E9F1] bg-white p-4 sm:p-5";

export function useMySchedule(enabled = true) {
  return useQuery({ queryKey: ["my-society-schedule"], queryFn: () => scheduleApi.my(), enabled, staleTime: 60_000, retry: false });
}

/** One line for a plan card: the next premium wash of this car's plan. */
export function NextPremiumWash({ subscriptionId, className = "" }: { subscriptionId: string; className?: string }) {
  const q = useMySchedule();
  const next = q.data?.next_by_subscription?.[subscriptionId];
  if (!next) return null;
  return (
    <p className={`flex items-center gap-1.5 text-xs font-semibold text-[#0E1A33] ${className}`} data-testid="next-premium-wash">
      <CalendarDays className="h-3.5 w-3.5 text-[#0A66F0]" />
      Next Premium Wash: {next.date_label}{next.slot_label ? `, ${next.slot_label}` : ""}{next.status === "planned" ? " (Planned)" : ""}
    </p>
  );
}

export function ResidentScheduleCard({ societyId }: { societyId?: string }) {
  const q = useMySchedule();
  const [asking, setAsking] = useState<MyScheduleItem | null>(null);
  if (q.isLoading || !q.data) return null;
  const items = q.data.items.filter((i) => !societyId || i.society_id === societyId);
  const pending = q.data.requests.filter((r) => r.status === "pending");
  const answered = q.data.requests.filter((r) => r.status !== "pending").slice(0, 2);
  if (!items.length && !pending.length) return null;
  const next = items[0];
  const slots = q.data.slots_by_society[societyId || next?.society_id || ""] || [];
  return (
    <section className={card} data-testid="resident-schedule">
      {next && (
        <div className="rounded-[14px] bg-[#E8F0FE] px-3.5 py-3">
          <p className="text-[12px] font-bold uppercase tracking-wide text-[#0A66F0]">Next Premium Wash</p>
          <p className="mt-0.5 text-[17px] font-extrabold text-[#0E1A33]" data-testid="next-premium-wash">
            {next.date_label}{next.slot_label ? `, ${next.slot_label}` : ""}
          </p>
          <p className="text-[13px] text-[#0A3E91]">{carsLine(next.cars)} · {next.status === "booked" ? "Booked" : `Planned — confirmed ${q.data.lead_days} day${q.data.lead_days === 1 ? "" : "s"} before`}</p>
        </div>
      )}
      {items.length > 1 && (
        <ul className="mt-3 space-y-2">
          {items.slice(1, 6).map((it) => (
            <li key={`${it.visit_id || it.booking_id}-${it.cars[0]?.sub_id}`} className="flex items-center justify-between gap-3 rounded-[12px] bg-[#F7F9FC] px-3 py-2.5 text-sm">
              <span className="min-w-0">
                <span className="block font-semibold text-[#0E1A33]">{it.date_label}{it.slot_label ? ` · ${it.slot_label}` : ""}</span>
                <span className="block text-xs text-[#5F6878]">{carsLine(it.cars)} · {it.status === "booked" ? "Booked" : "Planned"}</span>
              </span>
              {it.can_request && !pending.some((r) => r.visit_id === it.visit_id) && (
                <button type="button" className="shrink-0 text-xs font-bold text-[#0A66F0]" onClick={() => setAsking(it)}>Request Change</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {next?.can_request && !pending.some((r) => r.visit_id === next.visit_id) && (
        <button type="button" onClick={() => setAsking(next)}
          className="mt-3 flex h-11 w-full items-center justify-center rounded-[14px] border border-[#E4E9F1] text-sm font-bold text-[#0E1A33] hover:bg-[#EEF3FA]">
          Request A Change To {next.date_label}
        </button>
      )}
      {pending.map((r) => (
        <p key={r.id} className="mt-2 flex items-center gap-1.5 rounded-[12px] bg-[#FFF8DB] px-3 py-2 text-xs font-semibold text-[#7A5B00]">
          <Clock className="h-3.5 w-3.5" /> {r.kind === "skip" ? `Asked to skip ${r.visit_date_label}` : `Asked to move ${r.visit_date_label} to ${r.preferred_date_label}`} — waiting for your manager.
        </p>
      ))}
      {answered.map((r) => (
        <p key={r.id} className="mt-2 text-xs text-[#5F6878]">
          {r.visit_date_label}: {r.status === "approved" ? "Change Approved" : "Change Declined"}{r.resolution_note ? ` — ${r.resolution_note}` : ""}
        </p>
      ))}
      {asking && <ChangeForm item={asking} slots={slots} onDone={() => setAsking(null)} />}
    </section>
  );
}

function ChangeForm({ item, slots, onDone }: { item: MyScheduleItem; slots: { key: string; label: string }[]; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<"skip" | "move">("move");
  const [date, setDate] = useState(addDays(item.date, 1));
  const [slot, setSlot] = useState(item.slot_key || "");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const send = useMutation({
    mutationFn: () => scheduleApi.requestChange({
      visit_id: item.visit_id || "", kind, note: note || undefined,
      ...(kind === "move" ? { preferred_date: date, preferred_slot: slot || undefined } : {}),
    }),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["my-society-schedule"] }); onDone(); },
    onError: (err) => setError(getErrorMessage(err)),
  });
  const pill = (on: boolean) => `flex-1 rounded-[12px] border px-3 py-2 text-sm font-bold ${on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] text-[#0E1A33]"}`;
  return (
    <div className="mt-3 space-y-3 rounded-[14px] border border-[#E4E9F1] p-3" data-testid="schedule-change-form">
      <p className="text-sm font-bold text-[#0E1A33]">Change {item.date_label}</p>
      <div className="flex gap-2">
        <button type="button" className={pill(kind === "move")} onClick={() => setKind("move")}>Another Day</button>
        <button type="button" className={pill(kind === "skip")} onClick={() => setKind("skip")}>Skip It</button>
      </div>
      {kind === "move" && (
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="block text-xs font-semibold text-[#5F6878]">Preferred Date
            <input type="date" className="mt-1 h-11 w-full rounded-[12px] border border-[#E4E9F1] px-3 text-sm text-[#0E1A33]" min={addDays(todayIST(), 1)} max={addDays(todayIST(), 30)} value={date} onChange={(e) => setDate(e.target.value)} />
          </label>
          {slots.length > 0 && (
            <label className="block text-xs font-semibold text-[#5F6878]">Preferred Time
              <select className="mt-1 h-11 w-full rounded-[12px] border border-[#E4E9F1] bg-white px-3 text-sm text-[#0E1A33]" value={slot} onChange={(e) => setSlot(e.target.value)}>
                <option value="">Any Time</option>
                {slots.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
      <input className="h-11 w-full rounded-[12px] border border-[#E4E9F1] px-3 text-sm" maxLength={300} placeholder="Note for your manager (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
      {error && <p className="text-xs text-[#B91C1C]">{error}</p>}
      <div className="flex gap-2">
        <button type="button" className="h-11 flex-1 rounded-[12px] text-sm font-bold text-[#5F6878]" onClick={onDone}>Cancel</button>
        <button type="button" disabled={send.isPending || (kind === "move" && !date)} onClick={() => { setError(""); send.mutate(); }}
          className="h-11 flex-1 rounded-[12px] bg-[#0A66F0] text-sm font-bold text-white disabled:opacity-50">Send Request</button>
      </div>
    </div>
  );
}
