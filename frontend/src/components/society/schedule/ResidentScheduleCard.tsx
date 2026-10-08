/**
 * Resident: every premium wash coming up (booked + planned) with the next one
 * on top, "Change Or Skip" on planned days (the society manager approves),
 * and the answers to past requests. Mobile-first; also exports the one-line
 * NextPremiumWash for the customer app's plan card.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Clock } from "lucide-react";
import { getErrorMessage } from "../../../lib/api-client";
import { todayIST } from "../../../lib/date";
import { scheduleApi, type MySchedule, type MyScheduleItem } from "../../../api/societySchedule";
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

/** "MON / 19 / OCT" — the day at a glance (dates are IST calendar days). */
function DateBadge({ date, highlight }: { date: string; highlight: boolean }) {
  const d = new Date(`${date}T00:00:00+05:30`);
  const part = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", ...o }).format(d);
  return (
    <span className={`flex w-12 shrink-0 flex-col items-center rounded-[12px] py-1.5 leading-none ${highlight ? "bg-[#0A66F0] text-white" : "bg-white text-[#0E1A33] ring-1 ring-[#E4E9F1]"}`}>
      <span className="text-[10px] font-bold uppercase tracking-wide opacity-80">{part({ weekday: "short" })}</span>
      <span className="mt-0.5 text-[18px] font-extrabold">{part({ day: "numeric" })}</span>
      <span className="mt-0.5 text-[10px] font-semibold uppercase opacity-80">{part({ month: "short" })}</span>
    </span>
  );
}

const requestLine = (r: MySchedule["requests"][number]) =>
  r.kind === "skip" ? `Skip ${r.visit_date_label}` : `Move ${r.visit_date_label} to ${r.preferred_date_label}${r.preferred_slot_label ? `, ${r.preferred_slot_label}` : ""}`;

/**
 * One list of every premium wash coming up — booked ones and the days your
 * society's plan has set aside — with Change/Skip on the planned ones and
 * the answer to each change request right under the day it's about.
 */
export function ResidentScheduleCard({ societyId }: { societyId?: string }) {
  const q = useMySchedule();
  const [asking, setAsking] = useState<string | null>(null);
  if (q.isLoading || !q.data) return null;
  const items = q.data.items.filter((i) => !societyId || i.society_id === societyId);
  const pending = q.data.requests.filter((r) => r.status === "pending");
  const answered = q.data.requests.filter((r) => r.status !== "pending").slice(0, 3);
  const slots = q.data.slots_by_society[societyId || items[0]?.society_id || ""] || [];
  const lead = q.data.lead_days;
  const keyOf = (it: MyScheduleItem) => `${it.visit_id || it.booking_id}-${it.cars[0]?.sub_id}`;
  return (
    <section className={card} data-testid="resident-schedule">
      <h2 className="text-base font-bold text-[#0E1A33]">Your Premium Washes</h2>
      <p className="mt-0.5 text-[13px] text-[#5F6878]">
        <b className="font-semibold text-[#0E1A33]">Booked</b> washes are confirmed. <b className="font-semibold text-[#0E1A33]">Planned</b> days are set by your society&apos;s plan and confirmed {lead} day{lead === 1 ? "" : "s"} before — you can change or skip them.
      </p>
      {items.length === 0 ? (
        <p className="mt-3 rounded-[12px] bg-[#F7F9FC] px-3 py-3 text-sm text-[#5F6878]">No premium washes coming up. Book one below.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.slice(0, 8).map((it, idx) => {
            const k = keyOf(it);
            const waiting = pending.find((r) => r.visit_id && r.visit_id === it.visit_id);
            const next = idx === 0;
            return (
              <li key={k} className={`rounded-[14px] p-3 ${next ? "bg-[#E8F0FE]" : "bg-[#F7F9FC]"}`} data-testid={next ? "next-premium-wash" : undefined}>
                <div className="flex items-center gap-3">
                  <DateBadge date={it.date} highlight={next} />
                  <div className="min-w-0 flex-1">
                    {next && <p className="text-[11px] font-bold uppercase tracking-wide text-[#0A66F0]">Next Premium Wash</p>}
                    <p className="text-[15px] font-bold text-[#0E1A33]">{it.slot_label || "Time Set On The Day"}</p>
                    <p className="truncate text-xs text-[#5F6878]">{carsLine(it.cars)}</p>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${it.status === "booked" ? "bg-[#E7F6EC] text-[#15803D]" : "bg-white text-[#5F6878] ring-1 ring-[#E4E9F1]"}`}>
                    {it.status === "booked" ? "Booked" : "Planned"}
                  </span>
                </div>
                {waiting && (
                  <p className="mt-2 flex items-center gap-1.5 rounded-[10px] bg-[#FFF8DB] px-2.5 py-1.5 text-xs font-semibold text-[#7A5B00]">
                    <Clock className="h-3.5 w-3.5 shrink-0" /> You asked to {waiting.kind === "skip" ? "skip this day" : `move it to ${waiting.preferred_date_label}`} — waiting for your manager.
                  </p>
                )}
                {it.can_request && !waiting && asking !== k && (
                  <button type="button" className="mt-2 text-xs font-bold text-[#0A66F0] hover:underline" onClick={() => setAsking(k)}>
                    Change Or Skip This Day
                  </button>
                )}
                {asking === k && <ChangeForm item={it} slots={slots} onDone={() => setAsking(null)} />}
              </li>
            );
          })}
        </ul>
      )}
      {answered.length > 0 && (
        <div className="mt-3 border-t border-[#EEF2F7] pt-3">
          <p className="text-xs font-semibold text-[#5F6878]">Your Recent Requests</p>
          <ul className="mt-1.5 space-y-1">
            {answered.map((r) => (
              <li key={r.id} className="flex items-start justify-between gap-3 text-xs">
                <span className="text-[#0E1A33]">{requestLine(r)}{r.resolution_note ? ` — ${r.resolution_note}` : ""}</span>
                <span className={`shrink-0 font-bold ${r.status === "approved" ? "text-[#15803D]" : "text-[#B91C1C]"}`}>{r.status === "approved" ? "Approved" : "Declined"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
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
    <div className="mt-3 space-y-3 rounded-[14px] border border-[#E4E9F1] bg-white p-3" data-testid="schedule-change-form">
      <p className="text-sm font-bold text-[#0E1A33]">Change Or Skip {item.date_label}</p>
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
