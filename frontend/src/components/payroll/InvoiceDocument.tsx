import { format, formatDateTime } from "../../lib/date";
import { rupeesExact as rupeesText } from "./money";
import type { InvoiceLine, PayrollInvoice } from "../../api/payroll";
import { monthLabel } from "./payrollUi";

export const INVOICE_KIND_LABEL: Record<string, string> = {
  salary: "Salary",
  commission: "Commission",
  petrol: "Petrol & Expenses",
  advance: "Advance",
  other: "Other Payment",
};
const METHOD_LABEL: Record<string, string> = { cash: "Cash", upi: "UPI", bank: "Bank Transfer", other: "Other" };

const PRINT_CSS = `
@media print {
  @page { margin: 12mm; }
  body * { visibility: hidden !important; }
  .invoice-doc, .invoice-doc * { visibility: visible !important; }
  .invoice-doc { position: absolute; left: 0; top: 0; width: 100%; box-shadow: none !important; border: none !important; }
  .no-print { display: none !important; }
}`;

/**
 * The invoice / payslip as a printable document: Blussit header, captain and
 * center, the period, a lines table, totals, the method, the paid-on time in
 * 12-hour IST and a Paid (or Cancelled) stamp. Amounts are gross (no tax).
 */
export function InvoiceDocument({ invoice }: { invoice: PayrollInvoice }) {
  const lines = invoice.lines || [];
  const void_ = invoice.status === "void";
  const period =
    invoice.kind === "salary" && invoice.month
      ? monthLabel(invoice.month)
      : invoice.period_from
        ? invoice.period_from === invoice.period_to
          ? format(invoice.period_from)
          : `${format(invoice.period_from)} – ${format(invoice.period_to || invoice.period_from)}`
        : format(invoice.paid_on);
  const paidWhen = invoice.paid_at ? formatDateTime(invoice.paid_at) : format(invoice.paid_on);

  return (
    <>
      <style>{PRINT_CSS}</style>
      <article className="invoice-doc relative mx-auto w-full max-w-3xl rounded-2xl border border-[#F3E5B5] bg-white p-6 text-[#0E1A33] shadow-sm sm:p-10" aria-label={`Invoice ${invoice.number}`}>
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[#F3E5B5] pb-5">
          <div>
            <p className="text-2xl font-extrabold tracking-tight">Blussit</p>
            <p className="text-xs text-[#5F6878]">Doorstep Vehicle Care</p>
          </div>
          <div className="text-right">
            <p className="text-xs font-semibold uppercase tracking-wide text-[#5F6878]">Payment Invoice</p>
            <p className="font-mono-num text-lg font-bold">{invoice.number}</p>
            <p className="text-xs text-[#5F6878]">Issued {format(invoice.created_at)}</p>
          </div>
        </header>

        <div
          aria-hidden
          className={`pointer-events-none absolute right-4 top-[11.5rem] -rotate-12 rounded-lg border-4 px-3 py-0.5 text-xl font-extrabold sm:right-8 sm:top-[8.5rem] sm:px-4 sm:py-1 uppercase tracking-widest opacity-70 ${void_ ? "border-red-600 text-red-600 sm:text-2xl" : "border-green-600 text-green-700 sm:text-3xl"}`}
        >
          {void_ ? "Cancelled" : "Paid"}
        </div>

        <dl className="mt-5 grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-[#5F6878]">Paid To</dt>
            <dd className="mt-0.5 font-semibold">{invoice.captain_name || "Captain"}</dd>
            {invoice.captain_employee_id && <dd className="font-mono-num text-xs text-[#5F6878]">{invoice.captain_employee_id}</dd>}
            {invoice.service_center_name && <dd className="text-xs text-[#5F6878]">{invoice.service_center_name}</dd>}
          </div>
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-[#5F6878]">For</dt>
            <dd className="mt-0.5 font-semibold">{INVOICE_KIND_LABEL[invoice.kind] || invoice.kind}</dd>
            <dd className="text-xs text-[#5F6878]">{period}</dd>
          </div>
        </dl>

        <div className="mt-6 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-[#F3E5B5] text-left text-xs uppercase tracking-wide text-[#5F6878]">
                <th className="py-2 pr-3 font-semibold">Description</th>
                <th className="py-2 pl-3 text-right font-semibold">Amount</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={`${l.booking_id || l.type}-${i}`} className="border-b border-gray-100 align-top">
                  <td className="py-2 pr-3">
                    <LineText line={l} />
                  </td>
                  <td className={`py-2 pl-3 text-right font-mono-num font-semibold ${l.amount_paise < 0 ? "text-[var(--color-error)]" : ""}`}>{rupeesText(l.amount_paise)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <dl className="ml-auto mt-4 w-full max-w-xs space-y-1.5 text-sm">
          <Total label={invoice.kind === "salary" ? "Month Total" : "Total"} value={rupeesText(invoice.gross_paise)} />
          {invoice.previously_paid_paise > 0 && <Total label="Already Paid This Month" value={`− ${rupeesText(invoice.previously_paid_paise)}`} />}
          {invoice.kind === "salary" && <Total label="Balance Before This Payment" value={rupeesText(invoice.due_paise)} />}
          {invoice.adjustment_paise !== 0 && (
            <Total label={invoice.adjustment_paise > 0 ? "Added By Manager" : "Less By Manager"} value={`${invoice.adjustment_paise > 0 ? "+" : "−"} ${rupeesText(Math.abs(invoice.adjustment_paise))}`} />
          )}
          <Total label="Amount Paid" value={rupeesText(invoice.paid_paise)} strong />
          {invoice.cash_offset_paise > 0 && (
            <>
              <Total label="Set Against Cash Held" value={`− ${rupeesText(invoice.cash_offset_paise)}`} />
              <Total label="Handed Over" value={rupeesText(invoice.net_paise)} />
            </>
          )}
        </dl>
        {invoice.adjustment_reason && <p className="mt-3 text-xs text-[#5F6878]">Reason for the difference: {invoice.adjustment_reason}</p>}

        <footer className="mt-8 grid grid-cols-1 gap-3 border-t border-[#F3E5B5] pt-4 text-xs text-[#5F6878] sm:grid-cols-3">
          <p>
            <span className="block font-semibold uppercase tracking-wide">Method</span>
            <span className="text-sm font-semibold text-[#0E1A33]">{METHOD_LABEL[invoice.method] || invoice.method}</span>
          </p>
          <p>
            <span className="block font-semibold uppercase tracking-wide">Paid On</span>
            <span className="text-sm font-semibold text-[#0E1A33]">{paidWhen}</span>
          </p>
          <p>
            <span className="block font-semibold uppercase tracking-wide">Recorded By</span>
            <span className="text-sm font-semibold text-[#0E1A33]">{invoice.actor_name || "—"}</span>
          </p>
        </footer>
        {invoice.note && <p className="mt-3 text-xs text-[#5F6878]">Note: {invoice.note}</p>}
        {void_ && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-[var(--color-error)]">
            This invoice was cancelled{invoice.void?.by ? ` by ${invoice.void.by}` : ""}
            {invoice.void?.at ? ` on ${formatDateTime(invoice.void.at)}` : ""}
            {invoice.void?.reason ? `: ${invoice.void.reason}` : ""}. The money it covered is owed again.
          </p>
        )}
        <p className="mt-6 text-center text-[11px] text-[#5F6878]">Gross amounts, before any tax. This is a computer-generated payment record.</p>
      </article>
    </>
  );
}

function Total({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={`flex items-center justify-between gap-3 ${strong ? "border-t border-[#F3E5B5] pt-1.5 text-base font-bold" : ""}`}>
      <dt className="text-[#5F6878]">{label}</dt>
      <dd className="font-mono-num">{value}</dd>
    </div>
  );
}

function LineText({ line }: { line: InvoiceLine }) {
  if (line.type === "salary") {
    return (
      <>
        <p className="font-medium">{line.label || "Salary"}</p>
        <p className="text-xs text-[#5F6878]">
          {line.expected_working_days} working days · present {line.present_days}
          {line.half_days ? ` + ${line.half_days} half` : ""} · leave {line.leave_days} ({line.paid_leave_days} paid) · unpaid {line.unpaid_days} · holidays {line.holidays}
        </p>
        {line.computed_paise != null && line.computed_paise !== line.amount_paise && (
          <p className="text-xs text-[#5F6878]">Worked out from attendance: {rupeesText(line.computed_paise)}</p>
        )}
      </>
    );
  }
  if (line.type === "commission") {
    return (
      <>
        <p className="font-medium">
          <span className="font-mono-num">{line.booking_number}</span>
          {line.kind === "revised" ? " · Revised" : ""}
        </p>
        <p className="text-xs text-[#5F6878]">
          {[line.date ? format(line.date) : null, [line.vehicle_type, line.service_name].filter(Boolean).join(" · "), line.base_paise ? `Booking value ${rupeesText(line.base_paise)}` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </>
    );
  }
  return (
    <>
      <p className="font-medium">{line.label || line.type}</p>
      {line.date && <p className="text-xs text-[#5F6878]">{format(line.date)}</p>}
    </>
  );
}
