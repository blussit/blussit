import { useQuery } from "@tanstack/react-query";
import { adminServiceCenterApi } from "../../api/admin";
import { Input, Select } from "../ui";
import { payrollApi, type PayrollCaptainRow } from "../../api/payroll";
import { useAuth } from "../../context/AuthContext";
import { cleanAmountInput, rupeesText, toPaise } from "../../lib/paise";
import { MAX_COMMISSION_PERCENT } from "./PayFields";
import { currentMonth } from "./payrollUi";

/** Who washed it and what they earn — on a manager-done job (Log A Done Job, Mark As Done). */
export interface CommissionDraft {
  captainId: string;
  /** The captain's cut in whole rupees ("" = the captain's usual rate, or none for a non-commission captain). */
  amount: string;
}

export const emptyCommissionDraft = (): CommissionDraft => ({ captainId: "", amount: "" });

/**
 * Captain + commission only exist for self-operated centers: at a franchise
 * center the partner pays captains, so the fields are hidden and nothing is
 * sent. A center payload with no `operation` counts as self-operated.
 */
export function useCommissionEnabled(enabled = true): boolean {
  const { user } = useAuth();
  const centerId = user?.service_center_id;
  const query = useQuery({
    queryKey: ["center-detail-for-pay", centerId],
    queryFn: () => adminServiceCenterApi.get(centerId!),
    enabled: enabled && !!centerId,
    staleTime: 5 * 60 * 1000,
  });
  return query.data?.operation?.type !== "franchise";
}

/** The center's active captains with their pay terms (the manager's own center). */
export function useCenterPayCaptains(enabled = true) {
  return useQuery({
    queryKey: ["payroll-captains", "picker", currentMonth()],
    queryFn: () => payrollApi.captains({ month: currentMonth(), status: "active" }),
    enabled,
    staleTime: 60_000,
    select: (d) => d.rows,
  });
}

const rateLabel = (r: PayrollCaptainRow) =>
  r.pay.basis === "commission" ? (r.pay.commission.mode === "per_booking" ? "Set Per Booking" : `${r.pay.commission.percent}%`) : r.pay.basis === "salary" ? "Salary" : "Per-Job Fee";

const capOf = (baseRupees: number) => Math.floor((baseRupees * MAX_COMMISSION_PERCENT) / 100);

/** What the form must send (rupees go as paise here, at the edge). Never a commission with no captain. */
export function commissionPayload(d: CommissionDraft, captains: PayrollCaptainRow[] | undefined) {
  if (!d.captainId) return {};
  const cap = captains?.find((c) => c.id === d.captainId);
  const base = { captain_id: d.captainId };
  // Only a captain paid by commission can be given a commission (the server refuses it for the rest).
  if (cap?.pay.basis !== "commission") return base;
  const paise = toPaise(d.amount);
  return paise === null ? base : { ...base, commission_amount_paise: paise };
}

/** The first thing wrong with the captain/commission choice ("" = fine). The server's check wins. */
export function commissionError(d: CommissionDraft, captains: PayrollCaptainRow[] | undefined, baseRupees: number): string {
  if (!d.captainId) return "";
  const cap = captains?.find((c) => c.id === d.captainId);
  if (cap?.pay.basis !== "commission") return "";
  const paise = toPaise(d.amount);
  if (cap.pay.commission.mode === "per_booking" && (paise === null || paise <= 0)) return "Enter this captain's commission for the job in rupees.";
  if (paise !== null && baseRupees > 0 && paise > capOf(baseRupees) * 100) {
    return `Can't be more than ${MAX_COMMISSION_PERCENT}% (${rupeesText(capOf(baseRupees) * 100)}) of this job`;
  }
  return "";
}

/**
 * Optional "Captain" + "Captain's Commission (₹)" on a job the manager
 * logged or closed himself. No captain = nobody earns commission. `baseRupees`
 * is the job's amount shown on the form (tip excluded) for the live maximum —
 * the server works out the real one.
 */
export function CommissionFields({
  value,
  onChange,
  baseRupees,
  vehicles = 1,
}: {
  value: CommissionDraft;
  onChange: (next: CommissionDraft) => void;
  baseRupees: number;
  vehicles?: number;
}) {
  const enabled = useCommissionEnabled();
  const captains = useCenterPayCaptains(enabled);
  if (!enabled) return null;
  const rows = captains.data || [];
  const cap = rows.find((c) => c.id === value.captainId);
  const noCommission = !!cap && cap.pay.basis !== "commission";
  const perBooking = cap?.pay.basis === "commission" && cap.pay.commission.mode === "per_booking";
  const percentMode = cap?.pay.basis === "commission" && cap.pay.commission.mode === "percent";
  const maxRupees = capOf(baseRupees);
  const paise = toPaise(value.amount);
  const typedPct = paise !== null && baseRupees > 0 ? Math.round((paise / 100 / baseRupees) * 1000) / 10 : null;
  // The captain's usual rate as a rupee suggestion (the placeholder; empty = the server uses it).
  const suggested = percentMode && baseRupees > 0 ? Math.round((baseRupees * cap!.pay.commission.percent) / 100) : null;
  const problem = commissionError(value, rows, baseRupees);

  return (
    <div className="space-y-3">
      <Select label="Captain (Optional)" value={value.captainId} onChange={(e) => onChange({ captainId: e.target.value, amount: "" })}>
        <option value="">No Captain — I Did It Myself</option>
        {rows.map((c) => (
          <option key={c.id} value={c.id}>
            {c.full_name} · {rateLabel(c)}
          </option>
        ))}
      </Select>
      {captains.isError && <p className="text-xs text-[var(--color-error)]">Couldn't load your captains — you can still save without one.</p>}
      {!value.captainId && <p className="-mt-1 text-xs text-[var(--color-text-secondary)]">With no captain, no commission is paid.</p>}

      {noCommission && (
        <p className="rounded-xl bg-[#F7F9FC] p-3 text-xs text-[var(--color-text-secondary)]">
          {cap?.full_name} {cap?.pay.basis === "salary" ? "is on a salary" : "is still on the old per-job fee"}, so this job adds no commission.
        </p>
      )}

      {!!cap && !noCommission && (
        <Input
          label={vehicles > 1 ? "Captain's Commission For The Whole Visit (₹)" : "Captain's Commission (₹)"}
          inputMode="numeric"
          value={value.amount}
          onChange={(e) => onChange({ ...value, amount: cleanAmountInput(e.target.value) })}
          placeholder={suggested !== null ? String(suggested) : "300"}
          error={problem || undefined}
          hint={
            baseRupees > 0
              ? `Max ${rupeesText(maxRupees * 100)} — ${MAX_COMMISSION_PERCENT}% of this ${rupeesText(baseRupees * 100)} job.${typedPct !== null ? ` That is ${typedPct}%.` : ""}${
                  percentMode && suggested !== null && !value.amount ? ` Leave empty for ${cap!.full_name}'s usual ${cap!.pay.commission.percent}% (${rupeesText(suggested * 100)}).` : ""
                }${perBooking ? " Required — this captain's commission is set on each job." : ""}`
              : undefined
          }
        />
      )}
    </div>
  );
}
