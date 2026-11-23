import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { HandCoins, Undo2 } from "lucide-react";
import { Badge, Button, EmptyState, ErrorState } from "../ui";
import { payrollApi, type LedgerEntry, type LedgerKind, type PayrollCaptainDetail } from "../../api/payroll";
import { format, formatDateTime } from "../../lib/date";
import { rupeesText } from "../../lib/paise";
import { cn } from "../../lib/cn";
import { Banner, FRANCHISE_BANNER, LEDGER_LABEL, SkeletonRows } from "./payrollUi";
import { RecordPaymentModal, VoidPaymentModal } from "./PaymentModals";
import { InvoiceLink } from "./InvoiceLink";
import { PayWizard } from "./PayWizard";

const PAYMENT_KINDS: LedgerKind[] = ["payout", "advance", "payout_void"];
const PAYMENT_FOR: Record<string, string> = { salary: "Salary", commission: "Commission", petrol: "Petrol", advance: "Advance", other: "Other" };

/** The captain's ledger: payments by default, everything on request. Newest first. */
export function PaymentsTab({ detail }: { detail: PayrollCaptainDetail }) {
  const [paying, setPaying] = useState(false);
  const [other, setOther] = useState(false);
  const [voiding, setVoiding] = useState<LedgerEntry | null>(null);
  const [onlyPayments, setOnlyPayments] = useState(true);
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["payroll-ledger", detail.id, page],
    queryFn: () => payrollApi.ledger(detail.id, { page, page_size: 25 }),
    placeholderData: keepPreviousData,
  });

  const all = query.data?.data || [];
  const rows = onlyPayments ? all.filter((e) => PAYMENT_KINDS.includes(e.kind)) : all;
  const pages = query.data?.meta.total_pages || 1;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="radiogroup" aria-label="Show" className="flex gap-1.5">
          {[
            { v: true, label: "Payments" },
            { v: false, label: "Everything" },
          ].map((o) => (
            <button
              key={o.label}
              type="button"
              role="radio"
              aria-checked={onlyPayments === o.v}
              onClick={() => setOnlyPayments(o.v)}
              className={cn("rounded-full border px-3 py-1.5 text-xs font-semibold", onlyPayments === o.v ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] text-[#5F6878] hover:border-gray-400")}
            >
              {o.label}
            </button>
          ))}
        </div>
        <Button size="sm" onClick={() => setPaying(true)}>
          <HandCoins className="h-4 w-4" /> Pay
        </Button>
      </div>
      {!detail.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}

      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.isError && !query.data ? (
        <ErrorState message="Couldn't load the ledger." busy={query.isFetching} onRetry={() => void query.refetch()} />
      ) : !rows.length ? (
        <EmptyState icon={HandCoins} title={onlyPayments ? "No Payments Yet" : "Nothing In The Ledger Yet"} description="Payments you make show up here with their invoice, and stay here even if voided." />
      ) : (
        <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100">
          {rows.map((e) => {
            const isPayout = e.kind === "payout" || e.kind === "advance";
            return (
              <li key={e.id} className={cn("flex flex-wrap items-center justify-between gap-2 px-3 py-2.5", e.voided && "opacity-60")}>
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
                    {isPayout && e.payment_kind ? `${PAYMENT_FOR[e.payment_kind] || e.payment_kind} Payment` : LEDGER_LABEL[e.kind] || e.kind}
                    {e.booking_number && <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{e.booking_number}</span>}
                    {e.method && <Badge tone="neutral">{e.method.toUpperCase() === "UPI" ? "UPI" : e.method.charAt(0).toUpperCase() + e.method.slice(1)}</Badge>}
                    {e.invoice_number && (
                      <Badge tone="info">
                        <InvoiceLink numbers={[e.invoice_number]} ids={e.invoice_id ? [e.invoice_id] : undefined} />
                      </Badge>
                    )}
                    {e.voided && <Badge tone="error">Voided</Badge>}
                    {!e.finance_impact && isPayout && <Badge tone="neutral">Paid By Franchise</Badge>}
                  </p>
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    {e.entry_date ? format(e.entry_date) : formatDateTime(e.created_at)}
                    {e.actor_name ? ` · ${e.actor_name}` : ""}
                    {e.note ? ` · ${e.note}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={cn("font-mono-num text-sm font-semibold", isPayout ? "text-[var(--color-text-primary)]" : e.amount_paise < 0 ? "text-[var(--color-error)]" : "text-[var(--color-text-primary)]")}>
                    {rupeesText(isPayout ? Math.abs(e.amount_paise) : e.amount_paise)}
                  </span>
                  {isPayout && !e.voided && (
                    <Button size="sm" variant="ghost" aria-label={`Void payment of ${rupeesText(Math.abs(e.amount_paise))}`} onClick={() => setVoiding(e)}>
                      <Undo2 className="h-3.5 w-3.5" /> Void
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {pages > 1 && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}

      <PayWizard captain={detail} open={paying} onClose={() => setPaying(false)} onOther={() => { setPaying(false); setOther(true); }} />
      <RecordPaymentModal captain={detail} open={other} onClose={() => setOther(false)} />
      <VoidPaymentModal ledgerId={voiding?.id || null} label={voiding ? `This payment of ${rupeesText(Math.abs(voiding.amount_paise))}` : "This payment"} onClose={() => setVoiding(null)} />
    </div>
  );
}
