import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarCheck, Coins, FileText, HandCoins, PackageCheck, Plus, Wallet } from "lucide-react";
import { Button, ErrorState, Input, Modal } from "../ui";
import { payrollApi, type PayrollCaptainDetail, type PayrollStatement } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { cleanAmountInput, rupeesText, toPaise } from "../../lib/paise";
import { AttendanceTab } from "./AttendanceTab";
import { CommissionsTab } from "./CommissionsTab";
import { DepositKitTab } from "./DepositKitTab";
import { AdjustmentModal, RecordPaymentModal } from "./PaymentModals";
import { InvoicesTab } from "./InvoicesTab";
import { PayWizard } from "./PayWizard";
import { PaymentsTab } from "./PaymentsTab";
import { TermsTab } from "./TermsTab";
import { Banner, BasisBadge, Dues, Fact, FRANCHISE_BANNER, LEDGER_LABEL, MonthPicker, SkeletonRows, TabStrip, monthLabel, termsLine } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

export const DRAWER_TABS = [
  { key: "summary", label: "Summary" },
  { key: "attendance", label: "Attendance" },
  { key: "commissions", label: "Commissions" },
  { key: "payments", label: "Payments" },
  { key: "invoices", label: "Invoices" },
  { key: "terms", label: "Terms" },
  { key: "deposit", label: "Deposit & Kit" },
] as const;
export type DrawerTab = (typeof DRAWER_TABS)[number]["key"];

/**
 * One captain's pay, in a big dialog with six tabs. Used by the manager's
 * and the admin's Salary page alike — the server decides who may see whom.
 */
export function CaptainPayDrawer({
  captainId,
  month,
  onMonthChange,
  onClose,
  initialTab = "summary",
}: {
  captainId: string | null;
  month: string;
  onMonthChange: (month: string) => void;
  onClose: () => void;
  initialTab?: DrawerTab;
}) {
  return (
    <Modal open={!!captainId} onClose={onClose} title="Captain Pay" maxWidth="max-w-4xl">
      {captainId && <DrawerBody captainId={captainId} month={month} onMonthChange={onMonthChange} initialTab={initialTab} />}
    </Modal>
  );
}

function DrawerBody({ captainId, month, onMonthChange, initialTab }: { captainId: string; month: string; onMonthChange: (m: string) => void; initialTab: DrawerTab }) {
  const [tab, setTab] = useState<DrawerTab>(initialTab);
  const detailQuery = useQuery({ queryKey: ["payroll-captain", captainId], queryFn: () => payrollApi.captain(captainId) });
  const statementQuery = useQuery({ queryKey: ["payroll-statement", captainId, month], queryFn: () => payrollApi.statement(captainId, month) });
  const detail = detailQuery.data;

  if (detailQuery.isLoading) return <SkeletonRows rows={5} />;
  if (detailQuery.isError || !detail) {
    return <ErrorState message="Couldn't load this captain's pay." busy={detailQuery.isFetching} onRetry={() => void detailQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl bg-[var(--color-surface)] p-4">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-lg font-bold text-[var(--color-text-primary)]">
            {detail.full_name}
            {detail.employee_id && <span className="rounded-full bg-[#EEF3FA] px-2 py-0.5 font-mono-num text-[10px] font-bold text-[#0E1A33]">{detail.employee_id}</span>}
            <BasisBadge basis={detail.pay.basis} />
          </p>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            {termsLine(detail.pay)}
            {detail.service_center_name ? ` · ${detail.service_center_name}` : ""}
          </p>
        </div>
        <MonthPicker value={month} onChange={onMonthChange} />
      </div>

      {!detail.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}
      {detail.pay.basis === "legacy" && (
        <Banner tone="warning">
          This captain is still on the old per-job fee. Open the Terms tab to set salary or commission — nothing is rewritten for work already done.
        </Banner>
      )}

      <TabStrip tabs={DRAWER_TABS.map((t) => ({ ...t, badge: t.key === "commissions" && detail.pending_commissions > 0 }))} value={tab} onChange={setTab} />

      {tab === "summary" && <SummaryTab detail={detail} month={month} statement={statementQuery} onGo={setTab} />}
      {tab === "attendance" && <AttendanceTab detail={detail} month={month} statement={statementQuery} />}
      {tab === "commissions" && <CommissionsTab detail={detail} month={month} />}
      {tab === "payments" && <PaymentsTab detail={detail} />}
      {tab === "invoices" && <InvoicesTab captainId={detail.id} />}
      {tab === "terms" && <TermsTab detail={detail} />}
      {tab === "deposit" && <DepositKitTab detail={detail} />}
    </div>
  );
}

type StatementQuery = ReturnType<typeof useQuery<PayrollStatement>>;

function SummaryTab({ detail, month, statement, onGo }: { detail: PayrollCaptainDetail; month: string; statement: StatementQuery; onGo: (t: DrawerTab) => void }) {
  const [paying, setPaying] = useState(false);
  const [other, setOther] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const s = statement.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setPaying(true)}>
          <HandCoins className="h-4 w-4" /> Pay
        </Button>
        <Button size="sm" variant="outline" onClick={() => setOther(true)}>
          Advance Or Other
        </Button>
        <Button size="sm" variant="outline" onClick={() => setAdjusting(true)}>
          <Plus className="h-4 w-4" /> Add Adjustment
        </Button>
        {detail.pay.basis === "salary" && (
          <Button size="sm" variant="outline" onClick={() => setFinalizing(true)}>
            <FileText className="h-4 w-4" /> Finalize Month
          </Button>
        )}
      </div>

      {statement.isLoading ? (
        <SkeletonRows rows={2} />
      ) : statement.isError || !s ? (
        <ErrorState message="Couldn't load this month." busy={statement.isFetching} onRetry={() => void statement.refetch()} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Fact label={`Earned In ${monthLabel(month)}`}>
              <span className="font-mono-num">{rupeesText(s.earned_paise)}</span>
            </Fact>
            <Fact label={`Paid In ${monthLabel(month)}`}>
              <span className="font-mono-num">{rupeesText(s.paid_paise)}</span>
            </Fact>
            <Fact label="Dues Right Now (All Time)">
              <Dues paise={detail.dues_paise} className="text-base" />
            </Fact>
          </div>
          {detail.dues_paise < 0 && (
            <Banner tone="warning">
              {detail.pay.basis === "commission"
                ? "A booking was reduced or reversed after this captain was paid, or an advance was given. The amount is recovered from the next payment."
                : "This captain has been paid more than earned (an advance or a reversal). It is recovered from the next payment."}
            </Banner>
          )}

          <div className="rounded-2xl border border-gray-100 p-4">
            <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">How {monthLabel(month)} Adds Up</p>
            <dl className="space-y-1.5 text-sm">
              {detail.pay.basis === "salary" || s.salary_paise > 0 ? <Row label="Salary" value={rupeesText(s.salary_paise)} /> : null}
              {detail.pay.basis !== "salary" || s.commission_paise > 0 ? <Row label="Commission" value={rupeesText(s.commission_paise)} /> : null}
              {s.allowance_paise !== 0 && <Row label="Petrol And Allowances" value={rupeesText(s.allowance_paise)} />}
              {s.adjustments_paise !== 0 && <Row label="Bonus And Deductions" value={rupeesText(s.adjustments_paise)} />}
              <Row label="Total Earned" value={rupeesText(s.earned_paise)} strong />
            </dl>
          </div>

          {s.salary && (detail.pay.basis === "salary" || s.salary.finalized) && (
            <div className="rounded-2xl border border-gray-100 p-4">
              <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
                <CalendarCheck className="h-4 w-4 text-[#5F6878]" /> Salary From Attendance
                {s.salary.finalized && <span className="rounded-full bg-[#E8F0FE] px-2 py-0.5 text-[11px] font-semibold text-[#0A66F0]">Finalized</span>}
              </p>
              <dl className="space-y-1.5 text-sm">
                <Row label="Working Days In The Month" value={String(s.salary.expected_working_days)} />
                <Row label="Days Counted As Paid" value={`${s.salary.paid_days}`} />
                <Row label="Salary Per Day" value={rupeesText(s.salary.day_rate_paise)} />
                {s.salary.petrol_paise > 0 && <Row label="Petrol" value={rupeesText(s.salary.petrol_paise)} />}
                <Row label="Worked Out From Attendance" value={rupeesText(s.salary.computed_paise)} />
                {s.salary.finalized && s.salary.finalized_paise != null && <Row label="Finalized Amount" value={rupeesText(s.salary.finalized_paise)} strong />}
              </dl>
              {!s.salary.finalized && <p className="mt-2 text-xs text-[var(--color-text-secondary)]">Not finalized yet — finalize when the month is over so the salary is added to what the captain is owed.</p>}
            </div>
          )}

          {s.entries.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <p className="text-sm font-semibold text-[var(--color-text-primary)]">Recent Entries</p>
                <button type="button" className="text-xs font-semibold text-[#0A66F0] hover:underline" onClick={() => onGo("payments")}>
                  See All
                </button>
              </div>
              <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100">
                {s.entries.slice(0, 5).map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span className="min-w-0 truncate text-[var(--color-text-primary)]">
                      {LEDGER_LABEL[e.kind] || e.kind}
                      {e.booking_number ? ` · ${e.booking_number}` : ""}
                    </span>
                    <span className="font-mono-num font-semibold">{rupeesText(e.amount_paise)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="flex flex-wrap gap-4 text-xs text-[var(--color-text-secondary)]">
        <span className="inline-flex items-center gap-1">
          <Wallet className="h-3.5 w-3.5" /> Deposit Held: <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(detail.deposit_held_paise)}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <PackageCheck className="h-3.5 w-3.5" /> Kit: <span className="font-semibold text-[var(--color-text-primary)]">{!detail.kit_provided ? "None" : detail.kit_returned ? "Returned" : "With Captain"}</span>
        </span>
        {detail.cash_held_paise > 0 && (
          <span className="inline-flex items-center gap-1">
            <Coins className="h-3.5 w-3.5" /> Cash Held: <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(detail.cash_held_paise)}</span>
          </span>
        )}
      </div>

      <PayWizard captain={detail} open={paying} onClose={() => setPaying(false)} onOther={() => { setPaying(false); setOther(true); }} />
      <RecordPaymentModal captain={detail} open={other} onClose={() => setOther(false)} />
      <AdjustmentModal captain={detail} open={adjusting} onClose={() => setAdjusting(false)} />
      <FinalizeModal captainId={detail.id} month={month} computed={statement.data?.salary?.computed_paise} open={finalizing} onClose={() => setFinalizing(false)} />
    </div>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex items-center justify-between gap-3 ${strong ? "border-t border-gray-100 pt-1.5 font-semibold" : ""}`}>
      <dt className="text-[var(--color-text-secondary)]">{label}</dt>
      <dd className="font-mono-num text-[var(--color-text-primary)]">{value}</dd>
    </div>
  );
}

function FinalizeModal({ captainId, month, computed, open, onClose }: { captainId: string; month: string; computed?: number; open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={`Finalize ${monthLabel(month)}`}>
      <FinalizeForm captainId={captainId} month={month} computed={computed || 0} onClose={onClose} />
    </Modal>
  );
}

function FinalizeForm({ captainId, month, computed, onClose }: { captainId: string; month: string; computed: number; onClose: () => void }) {
  const write = usePayrollWrite();
  const [amount, setAmount] = useState(String(Math.round(computed / 100)));
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const payable = toPaise(amount);
  const changed = payable !== null && payable !== computed;
  const mutation = useMutation({
    mutationFn: () => payrollApi.finalize(captainId, month, changed ? { override_amount_paise: payable!, reason: reason.trim() } : {}),
    onSuccess: (res) => {
      write.ok(res.data.posted ? "Month Finalized" : "Already Up To Date", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        if (payable === null) return setError("Enter the salary in whole rupees.");
        if (changed && reason.trim().length < 3) return setError("A reason is required when you change the worked-out amount.");
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      <p className="text-sm text-[var(--color-text-secondary)]">
        Adds the salary for this month to what the captain is owed. Finalizing again later only adds the difference. Attendance worked out <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(computed)}</span>.
      </p>
      <Input label="Salary Payable (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} />
      {changed && <Input label="Reason For The Change" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Agreed to pay full month" />}
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="flex-1" isLoading={mutation.isPending}>
          Finalize Month
        </Button>
      </div>
    </form>
  );
}
