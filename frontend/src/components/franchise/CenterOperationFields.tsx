import { AlertTriangle, History } from "lucide-react";
import { Input, Switch } from "../ui";
import { format as formatDate, formatDateTime, todayIST } from "../../lib/date";
import type { OperationSnapshot, ServiceCenter, ServiceCenterOperation, ServiceCenterOperationType } from "../../types";
import type { ServiceCenterOperationInput } from "../../api/admin";
import { inr } from "./money";

/** The "Operation" section of the admin service-center form (§7.13 A.5). Every
 *  number is held as typed text and converted to paise / integers on save. */
export interface OperationForm {
  type: ServiceCenterOperationType;
  partnerName: string;
  partnerPhone: string;
  partnerEmail: string;
  sharePercent: string;
  feeRupees: string;
  feeReceived: boolean;
  kitsPurchased: string;
  kitsGiven: string;
  /** "yes" = the franchise pays captains; Blussit Finance records nothing. */
  franchisePaysCaptains: boolean;
  paySalary: boolean;
  defaultSalaryRupees: string;
  defaultCommissionPercent: string;
  effectiveFrom: string;
}

export const emptyOperation: OperationForm = {
  type: "self_operated",
  partnerName: "",
  partnerPhone: "",
  partnerEmail: "",
  sharePercent: "70",
  feeRupees: "",
  feeReceived: false,
  kitsPurchased: "",
  kitsGiven: "",
  franchisePaysCaptains: false,
  paySalary: false,
  defaultSalaryRupees: "",
  defaultCommissionPercent: "",
  effectiveFrom: "",
};

const text = (n: number | null | undefined) => (n == null ? "" : String(n));

export function operationFromCenter(op: ServiceCenterOperation | null | undefined): OperationForm {
  if (!op) return { ...emptyOperation };
  const pay = op.default_captain_pay;
  return {
    type: op.type === "franchise" ? "franchise" : "self_operated",
    partnerName: op.partner?.name ?? "",
    partnerPhone: op.partner?.phone ?? "",
    partnerEmail: op.partner?.email ?? "",
    sharePercent: op.revenue_share_percent != null ? String(op.revenue_share_percent) : "70",
    feeRupees: op.franchise_fee_paise ? String(Math.round(op.franchise_fee_paise / 100)) : "",
    feeReceived: false,
    kitsPurchased: text(op.kits_purchased),
    kitsGiven: text(op.kits_given),
    franchisePaysCaptains: op.captain_pay_managed_by === "franchise",
    paySalary: pay?.basis === "salary",
    defaultSalaryRupees: pay?.salary_monthly_paise ? String(Math.round(pay.salary_monthly_paise / 100)) : "",
    defaultCommissionPercent: pay?.commission_percent ? String(pay.commission_percent) : "",
    effectiveFrom: "",
  };
}

const WHOLE = /^\d{1,9}$/;
const PERCENT = /^\d{1,3}(\.\d{1,2})?$/;
const PHONE = /^\+?[0-9][0-9 -]{6,18}$/;
const MAX_FEE_RUPEES = 10_00_00_000; // ₹10 crore
const MAX_SALARY_RUPEES = 10_00_000; // ₹10 lakh a month
const MAX_KITS = 1000;

/** True when a change takes effect on a date — type, share or who pays captains. */
export function needsEffectiveFrom(original: ServiceCenterOperation | null | undefined, form: OperationForm): boolean {
  const o = operationFromCenter(original);
  if (o.type !== form.type) return true;
  if (form.type === "franchise") return Number(o.sharePercent) !== Number(form.sharePercent) || o.franchisePaysCaptains !== form.franchisePaysCaptains;
  return false;
}

/** First problem in the operation block (matching what the server will refuse), or "". */
export function validateOperation(f: OperationForm, original: ServiceCenterOperation | null | undefined, editing: boolean): string {
  if (f.type === "franchise") {
    if (f.partnerName.trim().length < 2) return "Enter the franchise partner's name.";
    if (f.partnerPhone.trim() && !PHONE.test(f.partnerPhone.trim())) return "Enter a valid partner phone number.";
    if (f.partnerEmail.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.partnerEmail.trim())) return "Enter a valid partner email, or leave it blank.";
    const share = Number(f.sharePercent);
    if (!PERCENT.test(f.sharePercent.trim()) || share <= 0 || share > 100) return "Revenue share to the partner must be more than 0 and at most 100 %.";
    if (f.feeRupees.trim() && (!WHOLE.test(f.feeRupees.trim()) || Number(f.feeRupees) > MAX_FEE_RUPEES)) return "Franchise fee must be a whole-rupee amount up to ₹10,00,00,000.";
    if (f.feeReceived && !editing && !(Number(f.feeRupees) > 0)) return "Enter the franchise fee before marking it received.";
    if (f.kitsPurchased.trim() && (!WHOLE.test(f.kitsPurchased.trim()) || Number(f.kitsPurchased) > MAX_KITS)) return "Kits purchased must be a whole number up to 1000.";
  } else {
    if (f.kitsGiven.trim() && (!WHOLE.test(f.kitsGiven.trim()) || Number(f.kitsGiven) > MAX_KITS)) return "Kits given must be a whole number up to 1000.";
    if (f.paySalary && (!WHOLE.test(f.defaultSalaryRupees.trim()) || Number(f.defaultSalaryRupees) <= 0 || Number(f.defaultSalaryRupees) > MAX_SALARY_RUPEES)) {
      return "Enter the default monthly salary in whole rupees, up to ₹10,00,000.";
    }
    if (!f.paySalary && f.defaultCommissionPercent.trim()) {
      const c = Number(f.defaultCommissionPercent);
      if (!PERCENT.test(f.defaultCommissionPercent.trim()) || c > 70) return "Default commission must be between 0 and 70 %.";
    }
  }
  if (editing && needsEffectiveFrom(original, f) && f.effectiveFrom && f.effectiveFrom < todayIST()) return "Effective From can't be in the past.";
  return "";
}

export function buildOperationPayload(f: OperationForm, editing: boolean, original: ServiceCenterOperation | null | undefined): ServiceCenterOperationInput {
  // Today by default; the server refuses a backdated day.
  const effective = editing && needsEffectiveFrom(original, f) ? { effective_from: f.effectiveFrom || todayIST() } : {};
  if (f.type === "franchise") {
    const fee = f.feeRupees.trim() ? Number(f.feeRupees) * 100 : 0;
    return {
      type: "franchise",
      partner: { name: f.partnerName.trim(), phone: f.partnerPhone.trim() || null, email: f.partnerEmail.trim() || null },
      revenue_share_percent: Number(f.sharePercent),
      franchise_fee_paise: fee,
      kits_purchased: f.kitsPurchased.trim() ? Number(f.kitsPurchased) : 0,
      captain_pay_managed_by: f.franchisePaysCaptains ? "franchise" : "blussit",
      // Fee receipts after the center exists go through a settlement, never this form.
      ...(!editing && f.feeReceived && fee > 0 ? { franchise_fee_received_now: true, franchise_fee_method: "bank" as const } : {}),
      ...effective,
    };
  }
  const commission = f.defaultCommissionPercent.trim() ? Number(f.defaultCommissionPercent) : null;
  return {
    type: "self_operated",
    kits_given: f.kitsGiven.trim() ? Number(f.kitsGiven) : 0,
    default_captain_pay: f.paySalary
      ? { basis: "salary", salary_monthly_paise: Number(f.defaultSalaryRupees) * 100 }
      : commission != null
        ? { basis: "commission", commission_percent: commission }
        : {},
    ...effective,
  };
}

/** Table/cards badge text. */
export const operationLabel = (op: Pick<ServiceCenterOperation | OperationSnapshot, "type"> | null | undefined) => (op?.type === "franchise" ? "Franchise" : "Self-Operated");

function Segmented<T extends string | boolean>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="inline-flex max-w-full flex-wrap rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={`rounded-[9px] px-4 py-1.5 text-sm font-medium transition ${value === o.value ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function CenterOperationFields({
  value,
  onChange,
  center,
}: {
  value: OperationForm;
  onChange: (next: OperationForm) => void;
  /** The center being edited (undefined when adding). */
  center?: ServiceCenter | null;
}) {
  const set = <K extends keyof OperationForm>(k: K, v: OperationForm[K]) => onChange({ ...value, [k]: v });
  const editing = !!center;
  const franchise = value.type === "franchise";
  const askDate = editing && needsEffectiveFrom(center?.operation, value);
  const kits = Number(franchise ? value.kitsPurchased : value.kitsGiven) || 0;
  const withKit = center?.kits_assigned;
  const history = center?.operation_history ?? [];

  return (
    <section className="space-y-4 rounded-xl border border-dashed border-gray-300 p-4" aria-label="Operation">
      <div>
        <p className="text-sm font-medium text-[var(--color-text-primary)]">Operation</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">Who runs this center, and how money and captain pay are handled.</p>
      </div>
      <Segmented
        label="Operation Type"
        value={value.type}
        onChange={(t) => set("type", t)}
        options={[
          { value: "self_operated", label: "Self-Operated" },
          { value: "franchise", label: "Franchise" },
        ]}
      />

      {franchise ? (
        <div className="space-y-4">
          <Input label="Partner Name" value={value.partnerName} maxLength={120} onChange={(e) => set("partnerName", e.target.value)} required />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Partner Phone" inputMode="tel" value={value.partnerPhone} maxLength={20} onChange={(e) => set("partnerPhone", e.target.value.replace(/[^\d+\s-]/g, ""))} />
            <Input label="Partner Email" type="email" value={value.partnerEmail} onChange={(e) => set("partnerEmail", e.target.value)} />
          </div>
          <Input
            label="Revenue Share To Partner (%)"
            inputMode="decimal"
            className="max-w-xs"
            value={value.sharePercent}
            onChange={(e) => set("sharePercent", e.target.value.replace(/[^\d.]/g, ""))}
            hint="Blussit keeps the rest"
            required
          />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Franchise Fee (₹)" inputMode="numeric" value={value.feeRupees} placeholder="0" onChange={(e) => set("feeRupees", e.target.value.split(".")[0].replace(/\D/g, ""))} />
            <Input label="Kits Purchased" inputMode="numeric" value={value.kitsPurchased} placeholder="0" onChange={(e) => set("kitsPurchased", e.target.value.split(".")[0].replace(/\D/g, ""))} />
          </div>
          {Number(value.feeRupees) > 0 &&
            (editing ? (
              <p className="text-xs text-[var(--color-text-secondary)]">Record fee payments from the Franchise page — each one is added to Finance as a credit.</p>
            ) : (
              <Switch
                checked={value.feeReceived}
                onChange={(v) => set("feeReceived", v)}
                label="Received Now"
                description={value.feeReceived ? `${inr(Number(value.feeRupees) * 100)} will be added to Finance as a credit.` : "Turn on once the partner has paid the fee."}
              />
            ))}
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Are Captains' Salaries Managed By The Franchise?</p>
            <Segmented
              label="Captain salaries managed by the franchise"
              value={value.franchisePaysCaptains}
              onChange={(v) => set("franchisePaysCaptains", v)}
              options={[
                { value: true, label: "Yes" },
                { value: false, label: "No" },
              ]}
            />
            <p className="mt-1.5 text-xs text-[var(--color-text-secondary)]">
              {value.franchisePaysCaptains
                ? "The partner pays the captains. The manager has no Salary page here; the admin can still record pay and invoices from Captain Salary, but nothing is counted in Blussit Finance."
                : "Blussit Finance pays captains and records it."}
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Input label="Kits Given" inputMode="numeric" className="max-w-xs" value={value.kitsGiven} placeholder="0" onChange={(e) => set("kitsGiven", e.target.value.split(".")[0].replace(/\D/g, ""))} />
          <div className="rounded-xl border border-[var(--color-card-border)] p-3">
            <p className="text-sm font-medium text-[var(--color-text-primary)]">Default For New Captains</p>
            <p className="mb-2 mt-0.5 text-xs text-[var(--color-text-secondary)]">Prefills the Add Captain form for this center. It never forces a captain's pay.</p>
            <p className="mb-1.5 text-sm text-[var(--color-text-primary)]">Pay Captains On Salary Basis?</p>
            <Segmented
              label="Pay captains on salary basis"
              value={value.paySalary}
              onChange={(v) => set("paySalary", v)}
              options={[
                { value: true, label: "Yes" },
                { value: false, label: "No" },
              ]}
            />
            <div className="mt-3">
              {value.paySalary ? (
                <Input
                  label="Default Monthly Salary (₹)"
                  inputMode="numeric"
                  className="max-w-xs"
                  value={value.defaultSalaryRupees}
                  onChange={(e) => set("defaultSalaryRupees", e.target.value.split(".")[0].replace(/\D/g, ""))}
                  hint="Prefilled when a manager adds a captain here"
                />
              ) : (
                <Input
                  label="Default Commission (%) — Optional"
                  inputMode="decimal"
                  className="max-w-xs"
                  value={value.defaultCommissionPercent}
                  onChange={(e) => set("defaultCommissionPercent", e.target.value.replace(/[^\d.]/g, ""))}
                  hint="Leave blank for no default"
                />
              )}
            </div>
          </div>
        </div>
      )}

      {editing && withKit != null && withKit !== kits && (
        <p role="status" title={center?.kits_warning ?? undefined} className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
          {franchise ? "Kits Purchased" : "Kits Given"} {kits} · Captains With Kit {withKit}
        </p>
      )}

      {askDate && (
        <div>
          <Input
            label="Effective From"
            type="date"
            className="max-w-xs"
            value={value.effectiveFrom}
            min={todayIST()}
            onChange={(e) => set("effectiveFrom", e.target.value)}
            placeholder="Today"
            hint="Leave blank for today. The new type, share and pay arrangement apply to bookings completed from this day."
          />
        </div>
      )}

      {history.length > 0 && (
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-secondary)]">
            <History className="h-3.5 w-3.5" aria-hidden /> Operation History
          </p>
          <ol className="space-y-1.5 border-l border-[var(--color-card-border)] pl-3">
            {history.map((h, i) => (
              <li key={`${h.effective_from ?? h.at ?? i}-${i}`} className="text-xs text-[var(--color-text-secondary)]">
                <span className="font-medium text-[var(--color-text-primary)]">{operationLabel(h.after)}</span>
                {h.after?.type === "franchise" && h.after.revenue_share_percent != null && <> · Partner {h.after.revenue_share_percent}%</>}
                {h.after?.type === "franchise" && <> · Captains {h.after.captain_pay_managed_by === "franchise" ? "Paid By Franchise" : "Paid By Blussit"}</>}
                {h.effective_from && <> · From {formatDate(h.effective_from)}</>}
                {h.at && <> · Saved {formatDateTime(h.at)}</>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}
