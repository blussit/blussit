/**
 * Society schedule — shared bits (theme v2: white, navy #0E1A33, blue
 * #0A66F0, muted #5F6878, tints #E8F0FE / #EEF3FA, hairlines #E4E9F1,
 * yellow #FFD21F for the one main call to action only).
 */
import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { cn } from "../../../lib/cn";
import { todayIST } from "../../../lib/date";
import type { Person } from "../../../api/society";
import { WEEKDAYS, WEEKDAYS_SHORT, type RulePattern, type SlotOption, type SocietyVisit } from "../../../api/societySchedule";

export const ui = {
  card: "rounded-[16px] border border-[#E4E9F1] bg-white",
  ink: "text-[#0E1A33]",
  muted: "text-[#5F6878]",
  label: "text-[12px] font-semibold uppercase tracking-wide text-[#5F6878]",
  btn: "inline-flex min-h-[36px] items-center justify-center gap-1.5 rounded-[10px] px-3 text-[13px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
  primary: "bg-[#0A66F0] text-white hover:bg-[#0852C2]",
  secondary: "bg-[#E8F0FE] text-[#0A66F0] hover:bg-[#D6E4FD]",
  outline: "border border-[#E4E9F1] bg-white text-[#0E1A33] hover:bg-[#EEF3FA]",
  ghost: "text-[#5F6878] hover:bg-[#EEF3FA] hover:text-[#0E1A33]",
  danger: "text-[#B91C1C] hover:bg-red-50",
  cta: "bg-[#FFD21F] text-[#0E1A33] hover:brightness-95",
  input: "h-10 w-full rounded-[10px] border border-[#E4E9F1] bg-white px-3 text-sm text-[#0E1A33] outline-none focus:border-[#0A66F0]",
};

export function Btn({ kind = "outline", className, children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "secondary" | "outline" | "ghost" | "danger" | "cta" }) {
  return (
    <button type="button" className={cn(ui.btn, ui[kind], className)} {...props}>
      {children}
    </button>
  );
}

export function Section({ title, description, actions, children, className }: { title: string; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn(ui.card, "p-4 sm:p-5", className)}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-[15px] font-bold text-[#0E1A33]">{title}</h2>
          {description && <p className="mt-0.5 text-[13px] text-[#5F6878]">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

const STATUS: Record<string, { label: string; cls: string }> = {
  planned: { label: "Planned", cls: "bg-[#EEF3FA] text-[#0E1A33]" },
  generating: { label: "Booking…", cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  booked: { label: "Booked", cls: "bg-[#E8F0FE] text-[#0A66F0]" },
  partial: { label: "Partly Booked", cls: "bg-amber-50 text-amber-800" },
  failed: { label: "Not Booked", cls: "bg-red-50 text-[#B91C1C]" },
  empty: { label: "No Cars Due", cls: "bg-[#EEF3FA] text-[#5F6878]" },
  skipped: { label: "Skipped", cls: "bg-[#F4F5F7] text-[#5F6878] line-through" },
  missed: { label: "Not Booked", cls: "bg-red-50 text-[#B91C1C]" },
  done: { label: "Done", cls: "bg-green-50 text-green-700" },
  cancelled: { label: "Removed", cls: "bg-[#F4F5F7] text-[#5F6878]" },
};

export function StatusChip({ visit }: { visit: Pick<SocietyVisit, "display_status"> }) {
  const s = STATUS[visit.display_status] || STATUS.planned;
  return <span className={cn("inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-bold", s.cls)}>{s.label}</span>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[13px] font-semibold text-[#0E1A33]">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-[#5F6878]">{hint}</span>}
    </label>
  );
}

/** A labelled group of toggles — NOT a <label>: a label wrapping buttons
 *  hands its text to the first button and clicks it from anywhere inside. */
export function FieldGroup({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div role="group" aria-label={label}>
      <span className="mb-1 block text-[13px] font-semibold text-[#0E1A33]">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-[#5F6878]">{hint}</span>}
    </div>
  );
}

export function Toggle({ on, onClick, children, disabled }: { on: boolean; onClick: () => void; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" aria-pressed={on} disabled={disabled} onClick={onClick}
      className={cn(
        "inline-flex min-h-[34px] items-center gap-1 rounded-full border px-3 text-[13px] font-semibold transition disabled:opacity-50",
        on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] bg-white text-[#0E1A33] hover:bg-[#EEF3FA]",
      )}>
      {on && <Check className="h-3.5 w-3.5" />} {children}
    </button>
  );
}

export function SlotPicker({ slots, value, onChange, single = false }: { slots: SlotOption[]; value: string[]; onChange: (v: string[]) => void; single?: boolean }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Slots">
      {slots.map((s) => (
        <Toggle key={s.key} on={value.includes(s.key)} onClick={() => {
          if (single) return onChange([s.key]);
          onChange(value.includes(s.key) ? value.filter((k) => k !== s.key) : [...value, s.key].sort());
        }}>{s.label}</Toggle>
      ))}
    </div>
  );
}

export function CaptainPicker({ captains, value, onChange, single = false }: { captains: Person[]; value: string[]; onChange: (v: string[]) => void; single?: boolean }) {
  if (!captains.length) return <p className="text-sm text-[#5F6878]">No Captains In This Center Yet</p>;
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Captains">
      {captains.map((c) => (
        <Toggle key={c.id} on={value.includes(c.id)} onClick={() => {
          if (single) return onChange(value.includes(c.id) ? [] : [c.id]);
          onChange(value.includes(c.id) ? value.filter((k) => k !== c.id) : [...value, c.id]);
        }}>{c.name || "Captain"}</Toggle>
      ))}
    </div>
  );
}

export function nextWeekday(weekday: number, from = todayIST()): string {
  const d = new Date(`${from}T12:00:00+05:30`);
  const today = (d.getUTCDay() + 6) % 7;
  const add = ((weekday - today + 7) % 7) || 7;
  d.setUTCDate(d.getUTCDate() + add);
  return d.toISOString().slice(0, 10);
}

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00+05:30`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function weekdayOf(day: string): number {
  return (new Date(`${day}T12:00:00+05:30`).getUTCDay() + 6) % 7;
}

/** Repeat pattern editor: every <day> / 1st & 3rd <day> / every N weeks. */
export function PatternEditor({ value, onChange }: { value: RulePattern; onChange: (p: RulePattern) => void }) {
  const set = (patch: Partial<RulePattern>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Repeat">
        <Toggle on={value.kind === "weekly"} onClick={() => set({ kind: "weekly" })}>Every Week</Toggle>
        <Toggle on={value.kind === "monthly_nth"} onClick={() => set({ kind: "monthly_nth", weeks: value.weeks?.length ? value.weeks : [1, 3] })}>Weeks Of The Month</Toggle>
        <Toggle on={value.kind === "every_n_weeks"} onClick={() => set({ kind: "every_n_weeks", interval_weeks: value.interval_weeks || 2, anchor_date: value.anchor_date || nextWeekday(value.weekday) })}>Every Few Weeks</Toggle>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Day">
        {WEEKDAYS_SHORT.map((d, i) => (
          <Toggle key={d} on={value.weekday === i} onClick={() => set({ weekday: i, anchor_date: value.kind === "every_n_weeks" ? nextWeekday(i) : value.anchor_date })}>{d}</Toggle>
        ))}
      </div>
      {value.kind === "monthly_nth" && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Weeks">
          {[1, 2, 3, 4, 5].map((w) => {
            const on = (value.weeks || []).includes(w);
            return <Toggle key={w} on={on} onClick={() => set({ weeks: on ? (value.weeks || []).filter((x) => x !== w) : [...(value.weeks || []), w].sort() })}>{["1st", "2nd", "3rd", "4th", "5th"][w - 1]}</Toggle>;
          })}
          <span className="text-[13px] text-[#5F6878]">{WEEKDAYS[value.weekday]} Of The Month</span>
        </div>
      )}
      {value.kind === "every_n_weeks" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Every">
            <select className={ui.input} value={value.interval_weeks || 2} onChange={(e) => set({ interval_weeks: Number(e.target.value) })}>
              {[1, 2, 3, 4, 5, 6, 8].map((n) => <option key={n} value={n}>{n === 1 ? "Week" : `${n} Weeks`}</option>)}
            </select>
          </Field>
          <Field label="Starting" hint={`Must be a ${WEEKDAYS[value.weekday]}.`}>
            <input type="date" className={ui.input} value={value.anchor_date || ""} min={addDays(todayIST(), 1)}
              onChange={(e) => set({ anchor_date: e.target.value, weekday: e.target.value ? weekdayOf(e.target.value) : value.weekday })} />
          </Field>
        </div>
      )}
    </div>
  );
}

export function Banner({ tone = "info", children }: { tone?: "info" | "warn" | "error"; children: ReactNode }) {
  const cls = tone === "warn" ? "bg-amber-50 text-amber-900 border-amber-200" : tone === "error" ? "bg-red-50 text-[#B91C1C] border-red-200" : "bg-[#E8F0FE] text-[#0A3E91] border-[#D6E4FD]";
  return <div className={cn("rounded-[12px] border px-3 py-2 text-[13px]", cls)}>{children}</div>;
}
