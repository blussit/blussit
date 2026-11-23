import { Plus, Trash2 } from "lucide-react";
import { Input, Select, Switch } from "../ui";
import { cn } from "../../lib/cn";
import { cleanAmountInput, rupeesInput, toPaise } from "../../lib/paise";
import { PAY_LIMITS, type CommissionMode, type DefaultCaptainPay, type KitItem, type NewCaptainPay, type PayKit } from "../../api/payroll";
import { WEEKDAYS } from "./payrollUi";

export const MAX_COMMISSION_PERCENT = 70;

export const KIT_QUICK_ADD = ["Vacuum", "Jet Pressure Washer", "Polisher", "Cloths Set"] as const;

export interface KitItemDraft {
  name: string;
  qty: string;
  serial: string;
  value: string;
}

/** What a person is typing in the Pay section — strings, whole rupees. */
export interface PayDraft {
  basis: "" | "salary" | "commission";
  salary: string;
  weekly_off: string;
  paid_leaves: string;
  petrol: string;
  mode: CommissionMode;
  percent: string;
  deposit: string;
  deposit_received: boolean;
  kit_provided: "" | "yes" | "no";
  items: KitItemDraft[];
}

export const emptyKitItem = (name = ""): KitItemDraft => ({ name, qty: "1", serial: "", value: "" });

/** Fresh draft, prefilled from the center's default captain pay (never forced). */
export function emptyPayDraft(def?: DefaultCaptainPay | null): PayDraft {
  const basis = def?.basis === "salary" || def?.basis === "commission" ? def.basis : "";
  return {
    basis,
    salary: basis === "salary" ? rupeesInput(def?.salary_monthly_paise) : "",
    weekly_off: "6",
    paid_leaves: "1",
    petrol: "",
    mode: "percent",
    percent: basis === "commission" && def?.commission_percent != null ? String(def.commission_percent) : "",
    deposit: "",
    deposit_received: false,
    kit_provided: "",
    items: [],
  };
}

export function kitItemsFromDraft(items: KitItemDraft[]): KitItem[] {
  return items
    .filter((i) => i.name.trim())
    .map((i) => ({
      name: i.name.trim(),
      qty: Math.max(1, Math.round(Number(i.qty) || 1)),
      serial: i.serial.trim() || null,
      value_paise: toPaise(i.value) ?? 0,
    }));
}

export function kitDraftFromItems(items: KitItem[] | undefined): KitItemDraft[] {
  return (items || []).map((i) => ({ name: i.name, qty: String(i.qty || 1), serial: i.serial || "", value: rupeesInput(i.value_paise) }));
}

/** The first thing wrong with the Pay section, said plainly ("" = fine). */
export function payDraftError(d: PayDraft, maxPercent = MAX_COMMISSION_PERCENT): string {
  if (!d.basis) return "Choose how this captain is paid — Salary or Commission.";
  if (d.basis === "salary") {
    const salary = toPaise(d.salary);
    if (!salary || salary <= 0) return "Enter the monthly salary in rupees.";
    const leaves = Number(d.paid_leaves);
    if (!Number.isInteger(leaves) || leaves < 0 || leaves > PAY_LIMITS.paidLeavesMax) return `Paid leaves per month must be a whole number from 0 to ${PAY_LIMITS.paidLeavesMax}.`;
    if (d.petrol.trim() && toPaise(d.petrol) === null) return "Petrol per day must be a whole number of rupees.";
  } else if (d.mode === "percent") {
    const pct = Number(d.percent);
    if (d.percent.trim() === "" || !Number.isFinite(pct) || pct < 0 || pct > maxPercent) return `Enter a commission percent from 0 to ${maxPercent}.`;
  }
  if (d.deposit.trim() && toPaise(d.deposit) === null) return "Deposit must be a whole number of rupees.";
  if (!d.kit_provided) return "Say whether a kit is provided — Yes or No.";
  if (d.kit_provided === "yes" && !kitItemsFromDraft(d.items).length) return "Add at least one kit item, or choose No.";
  return "";
}

export function payDraftToPayload(d: PayDraft): NewCaptainPay {
  const deposit = toPaise(d.deposit) || 0;
  const base: NewCaptainPay = {
    basis: d.basis as "salary" | "commission",
    deposit_expected_paise: deposit || undefined,
    deposit_received: deposit > 0 ? d.deposit_received : undefined,
    kit: { provided: d.kit_provided === "yes", items: d.kit_provided === "yes" ? kitItemsFromDraft(d.items) : [] },
  };
  if (d.basis === "salary") {
    return {
      ...base,
      salary_monthly_paise: toPaise(d.salary) || 0,
      weekly_off: Number(d.weekly_off),
      paid_leaves_per_month: Number(d.paid_leaves) || 0,
      petrol_per_day_paise: toPaise(d.petrol) || undefined,
    };
  }
  return { ...base, commission: { mode: d.mode, percent: d.mode === "percent" ? Number(d.percent) : 0 } };
}

const CHOICE =
  "flex min-h-11 flex-1 items-center justify-center rounded-xl border-2 px-3 text-sm font-semibold transition-colors";
const choiceClass = (on: boolean) => (on ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0E1A33]" : "border-gray-200 bg-white text-[#5F6878] hover:border-gray-400");

export function ChoicePair<T extends string>({ label, value, options, onChange }: { label: string; value: T | ""; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div>
      <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">{label}</p>
      <div role="radiogroup" aria-label={label} className="flex gap-2">
        {options.map((o) => (
          <button key={o.value} type="button" role="radio" aria-checked={value === o.value} onClick={() => onChange(o.value)} className={cn(CHOICE, choiceClass(value === o.value))}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Kit item rows with quick-add chips — shared by Add Captain and the Deposit & Kit tab. */
export function KitItemsEditor({ items, onChange }: { items: KitItemDraft[]; onChange: (items: KitItemDraft[]) => void }) {
  const has = (name: string) => items.some((i) => i.name.trim().toLowerCase() === name.toLowerCase());
  const patch = (idx: number, p: Partial<KitItemDraft>) => onChange(items.map((it, i) => (i === idx ? { ...it, ...p } : it)));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {KIT_QUICK_ADD.map((name) => (
          <button
            key={name}
            type="button"
            disabled={has(name)}
            onClick={() => onChange([...items, emptyKitItem(name)])}
            className="inline-flex items-center gap-1 rounded-full border border-[#E4E9F1] bg-white px-3 py-1.5 text-xs font-semibold text-[#0E1A33] transition-colors hover:border-[#0A66F0] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-3 w-3" /> {name}
          </button>
        ))}
      </div>
      {items.map((it, idx) => (
        <div key={idx} className="space-y-2 rounded-xl border border-gray-100 p-3">
          <div className="flex items-end gap-2">
            <Input label="Item" value={it.name} maxLength={60} onChange={(e) => patch(idx, { name: e.target.value })} placeholder="Vacuum" />
            <button
              type="button"
              aria-label={`Remove ${it.name || "item"}`}
              onClick={() => onChange(items.filter((_, i) => i !== idx))}
              className="mb-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-[var(--color-error)] hover:bg-red-50"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Input label="Qty" inputMode="numeric" value={it.qty} onChange={(e) => patch(idx, { qty: cleanAmountInput(e.target.value).slice(0, 3) })} />
            <Input label="Serial No." value={it.serial} maxLength={40} onChange={(e) => patch(idx, { serial: e.target.value })} />
            <Input label="Value (₹)" inputMode="numeric" value={it.value} onChange={(e) => patch(idx, { value: cleanAmountInput(e.target.value) })} />
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, emptyKitItem()])}
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#0A66F0] hover:underline"
      >
        <Plus className="h-4 w-4" /> Add Another Item
      </button>
    </div>
  );
}

/**
 * The "Pay" section of the Add Captain forms: how the captain is paid, the
 * deposit, and the kit. Every field can be changed later from the Salary
 * page's Terms and Deposit & Kit tabs.
 */
export function PayFields({ value, onChange, prefilled }: { value: PayDraft; onChange: (next: PayDraft) => void; prefilled?: boolean }) {
  const set = (p: Partial<PayDraft>) => onChange({ ...value, ...p });
  return (
    <fieldset className="space-y-4 rounded-2xl border border-[#E4E9F1] p-4">
      <legend className="px-1.5 text-sm font-semibold text-[#0E1A33]">Pay</legend>
      {prefilled && <p className="text-xs text-[var(--color-text-secondary)]">Filled from this center's default pay — change anything you need.</p>}

      <ChoicePair
        label="Pay Basis"
        value={value.basis}
        options={[
          { value: "salary", label: "Salary" },
          { value: "commission", label: "Commission" },
        ]}
        onChange={(basis) => set({ basis })}
      />

      {value.basis === "salary" && (
        <div className="space-y-3">
          <Input label="Monthly Salary (₹)" inputMode="numeric" value={value.salary} onChange={(e) => set({ salary: cleanAmountInput(e.target.value) })} placeholder="15000" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Select label="Working Off Day" value={value.weekly_off} onChange={(e) => set({ weekly_off: e.target.value })}>
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={String(i)}>
                  {d}
                </option>
              ))}
            </Select>
            <Input label="Paid Leaves Per Month" inputMode="numeric" value={value.paid_leaves} onChange={(e) => set({ paid_leaves: cleanAmountInput(e.target.value).slice(0, 2) })} />
          </div>
          <Input
            label="Petrol Per Day (₹, Optional)"
            inputMode="numeric"
            value={value.petrol}
            onChange={(e) => set({ petrol: cleanAmountInput(e.target.value) })}
            hint="Added for every day the captain is present."
          />
        </div>
      )}

      {value.basis === "commission" && (
        <div className="space-y-3">
          <ChoicePair
            label="Commission Type"
            value={value.mode}
            options={[
              { value: "percent", label: "Fixed Percent" },
              { value: "per_booking", label: "Per Booking" },
            ]}
            onChange={(mode) => set({ mode })}
          />
          {value.mode === "percent" ? (
            <Input
              label={`Commission Percent (0–${MAX_COMMISSION_PERCENT})`}
              inputMode="decimal"
              value={value.percent}
              onChange={(e) => set({ percent: e.target.value.replace(/[^\d.]/g, "").slice(0, 5) })}
              hint="Of each completed booking. Changing it later affects future bookings only."
              placeholder="20"
            />
          ) : (
            <p className="rounded-xl bg-[#F7F9FC] p-3 text-xs text-[var(--color-text-secondary)]">
              You set the commission on each booking yourself — until you do, it shows as "Commission Not Set".
            </p>
          )}
        </div>
      )}

      <div className="space-y-2">
        <Input label="Deposit Amount (₹, Optional)" inputMode="numeric" value={value.deposit} onChange={(e) => set({ deposit: cleanAmountInput(e.target.value) })} placeholder="0" />
        {!!toPaise(value.deposit) && <Switch checked={value.deposit_received} onChange={(deposit_received) => set({ deposit_received })} label="Received Now?" description="Switch on if the deposit has already been handed over." />}
      </div>

      <div className="space-y-3">
        <ChoicePair
          label="Kit Provided"
          value={value.kit_provided}
          options={[
            { value: "yes", label: "Yes" },
            { value: "no", label: "No" },
          ]}
          onChange={(kit_provided) => set({ kit_provided, items: kit_provided === "yes" && !value.items.length ? [emptyKitItem()] : value.items })}
        />
        {value.kit_provided === "yes" && <KitItemsEditor items={value.items} onChange={(items) => set({ items })} />}
      </div>
    </fieldset>
  );
}

/** Read-only summary of a kit — "2 × Vacuum, Polisher". */
export function kitSummary(kit: PayKit | undefined | null): string {
  if (!kit?.provided) return "No kit";
  const names = (kit.items || []).map((i) => (i.qty > 1 ? `${i.qty} × ${i.name}` : i.name));
  return names.length ? names.join(", ") : "Kit provided";
}
