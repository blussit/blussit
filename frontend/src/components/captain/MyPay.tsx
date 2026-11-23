/**
 * Captain · My Pay — what the captain earned this month, what was paid, what
 * is still owed, with the bookings behind it. Shown in place of the old
 * per-job fee numbers for captains on salary or commission. Phone-first,
 * English + Hindi (captain i18n).
 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { payrollApi, type AttendanceStatus, type PayrollMe } from "../../api/payroll";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { formatShortDate } from "../../lib/date";
import { rupeesText } from "../../lib/paise";
import { rupeesExact } from "../payroll/money";
import { monthLabel } from "../payroll/payrollUi";
import { kitSummary } from "../payroll/PayFields";
import { LoadError, Notice, PageTitle, Panel, Pill, StatTile } from "./ui";

/** The captain's own pay — one shared read; `null` data = legacy or not loaded. */
export function useMyPay() {
  return useQuery({ queryKey: ["payroll-me"], queryFn: payrollApi.me, staleTime: 30_000, retry: 1 });
}

/** True when this captain is paid by salary or commission (not the old per-job fee). */
export const isManagedPay = (pay?: PayrollMe | null) => !!pay && (pay.basis === "salary" || pay.basis === "commission");

export function MyPay({ pay }: { pay: PayrollMe }) {
  const { t } = useCaptainTranslation();
  const fill = (text: string, vars: Record<string, string | number>) => Object.entries(vars).reduce((s, [k, v]) => s.replace(`{${k}}`, String(v)), text);

  const terms =
    pay.basis === "salary"
      ? fill(t("captain.mypay.perMonth"), { amount: rupeesText(pay.pay.salary_monthly_paise) })
      : pay.pay.commission.mode === "per_booking"
        ? t("captain.mypay.setPerBooking")
        : fill(t("captain.mypay.percentEach"), { pct: pay.pay.commission.percent });

  const count = (status: AttendanceStatus) => pay.attendance.filter((d) => d.status === status).length;
  const payments = pay.entries.filter((e) => (e.kind === "payout" || e.kind === "advance") && !e.voided);
  const kit = pay.pay.kit;
  const negative = pay.dues_paise < 0;

  return (
    <div className="space-y-4">
      <PageTitle sub={monthLabel(pay.month)}>{t("captain.mypay.title")}</PageTitle>

      <Panel className="flex items-center justify-between gap-3 px-4 py-3.5">
        <div className="min-w-0">
          <p className="text-[13px] font-bold text-[#5F6878]">{pay.basis === "salary" ? t("captain.mypay.basisSalary") : t("captain.mypay.basisCommission")}</p>
          <p className="text-[17px] font-extrabold text-[#0E1A33]">{terms}</p>
        </div>
        <Pill tone="blue">{t("captain.mypay.month")}</Pill>
      </Panel>

      <div className="grid grid-cols-3 gap-2">
        <StatTile label={t("captain.mypay.earned")} value={rupeesText(pay.earned_paise)} />
        <StatTile label={t("captain.mypay.paid")} value={rupeesText(pay.paid_paise)} tone="green" />
        <StatTile label={negative ? t("captain.mypay.recovery") : t("captain.mypay.dues")} value={rupeesText(Math.abs(pay.dues_paise))} tone="blue" />
      </div>
      {negative ? (
        <Notice tone="amber">{fill(t("captain.mypay.recoveryHint"), { amount: rupeesText(Math.abs(pay.dues_paise)) })}</Notice>
      ) : (
        <p className="px-1 text-xs font-semibold text-[#5F6878]">{t("captain.mypay.duesHint")}</p>
      )}

      {pay.basis === "commission" && (
        <section>
          <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.mypay.commissions")}</h2>
          {!pay.commissions.length ? (
            <Panel className="px-4 py-6 text-center text-sm font-semibold text-[#5F6878]">{t("captain.mypay.noCommissions")}</Panel>
          ) : (
            <Panel className="divide-y divide-[#E4E9F1]">
              {pay.commissions.map((c) => (
                <div key={c.id} className="flex min-h-[56px] items-center justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="tabular-nums text-[15px] font-bold text-[#0E1A33]">{c.booking_number}</p>
                    <p className="text-[13px] text-[#5F6878]">
                      {c.completed_at ? formatShortDate(c.completed_at) : ""}
                      {c.rate_percent != null && !c.not_set ? ` · ${c.rate_percent}%` : ""}
                    </p>
                    {!c.not_set && c.payment_state && c.payment_state !== "none" && (
                      <div className="mt-1">
                        {c.payment_state === "paid" ? (
                          <Pill tone="green">{fill(t("captain.mypay.paidInvoice"), { no: (c.invoice_numbers || []).join(", ") })}</Pill>
                        ) : (
                          <Pill tone="amber">{c.payment_state === "part_paid" ? t("captain.mypay.partPaid") : t("captain.mypay.unpaid")}</Pill>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {c.edited && <Pill tone="blue">{t("captain.mypay.edited")}</Pill>}
                    {c.not_set ? <Pill tone="amber">{t("captain.mypay.notSet")}</Pill> : <span className="tabular-nums text-[16px] font-extrabold text-[#0E1A33]">{rupeesText(c.amount_paise)}</span>}
                  </div>
                </div>
              ))}
            </Panel>
          )}
        </section>
      )}

      {pay.basis === "salary" && (
        <section>
          <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.mypay.attendance")}</h2>
          <div className="grid grid-cols-4 gap-2">
            <StatTile label={t("captain.mypay.present")} value={count("present")} tone="green" />
            <StatTile label={t("captain.mypay.halfDay")} value={count("half_day")} />
            <StatTile label={t("captain.mypay.leave")} value={count("leave")} tone="blue" />
            <StatTile label={t("captain.mypay.absent")} value={count("absent")} />
          </div>
          {pay.salary && <p className="mt-1.5 px-1 text-xs font-semibold text-[#5F6878]">{fill(t("captain.mypay.expectedDays"), { n: pay.salary.expected_working_days })}</p>}
        </section>
      )}

      <section>
        <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.mypay.payments")}</h2>
        {!payments.length ? (
          <Panel className="px-4 py-6 text-center text-sm font-semibold text-[#5F6878]">{t("captain.mypay.noPayments")}</Panel>
        ) : (
          <Panel className="divide-y divide-[#E4E9F1]">
            {payments.slice(0, 10).map((e) => (
              <div key={e.id} className="flex min-h-[52px] items-center justify-between gap-3 px-4 py-2.5">
                <p className="text-[14px] font-semibold text-[#5F6878]">{formatShortDate(e.entry_date || e.created_at)}</p>
                <span className="tabular-nums text-[16px] font-extrabold text-[#0E1A33]">{rupeesExact(Math.abs(e.amount_paise))}</span>
              </div>
            ))}
          </Panel>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.mypay.invoices")}</h2>
        {!(pay.invoices || []).length ? (
          <Panel className="px-4 py-6 text-center text-sm font-semibold text-[#5F6878]">{t("captain.mypay.noInvoices")}</Panel>
        ) : (
          <Panel className="divide-y divide-[#E4E9F1]">
            {(pay.invoices || []).slice(0, 10).map((inv) => (
              <Link key={inv.id} to={`/captain/earnings/invoice/${inv.id}`} className={`flex min-h-[56px] items-center justify-between gap-3 px-4 py-2.5 ${inv.status === "void" ? "opacity-50" : ""}`}>
                <div className="min-w-0">
                  <p className="tabular-nums text-[14px] font-bold text-[#0E1A33]">{inv.number}</p>
                  <p className="text-[13px] text-[#5F6878]">{formatShortDate(inv.paid_on)}{inv.status === "void" ? ` · ${t("captain.mypay.cancelled")}` : ""}</p>
                </div>
                <span className="tabular-nums text-[16px] font-extrabold text-[#0E1A33]">{rupeesExact(inv.paid_paise)}</span>
              </Link>
            ))}
          </Panel>
        )}
      </section>

      <Panel className="divide-y divide-[#E4E9F1]">
        <div className="px-4 py-3">
          <p className="text-[13px] font-bold text-[#5F6878]">{t("captain.mypay.deposit")}</p>
          <p className="text-[15px] font-bold text-[#0E1A33]">
            {pay.deposit_held_paise > 0 || pay.pay.deposit_expected_paise > 0 ? fill(t("captain.mypay.depositHeld"), { amount: rupeesText(pay.deposit_held_paise) }) : t("captain.mypay.depositNone")}
          </p>
        </div>
        <div className="px-4 py-3">
          <p className="text-[13px] font-bold text-[#5F6878]">{t("captain.mypay.kit")}</p>
          <p className="text-[15px] font-bold text-[#0E1A33]">
            {!kit?.provided ? t("captain.mypay.kitNone") : kit.returned_on ? t("captain.mypay.kitReturned") : fill(t("captain.mypay.kitWithYou"), { items: kitSummary(kit).replace(/^Kit provided$/, "") })}
          </p>
        </div>
      </Panel>
      <p className="px-1 pb-2 text-xs font-semibold text-[#5F6878]">{t("captain.mypay.ledgerNote")}</p>
    </div>
  );
}

/** Whole-screen version with its own loading / error states — the Earnings tab uses `useMyPay` itself. */
export function MyPayLoadError({ busy, onRetry }: { busy: boolean; onRetry: () => void }) {
  const { t } = useCaptainTranslation();
  return <LoadError title={t("captain.mypay.loadFailed")} sub={t("captain.v2.loadFailedSub")} retryLabel={t("captain.v2.tryAgain")} busy={busy} onRetry={onRetry} />;
}
