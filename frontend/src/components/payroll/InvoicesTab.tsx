import { useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { FileText, Undo2 } from "lucide-react";
import { adminServiceCenterApi } from "../../api/admin";
import { payrollApi, type InvoiceListFilters, type PayrollInvoice } from "../../api/payroll";
import { Badge, Button, EmptyState, ErrorState, Input, Modal, Select, StatCard } from "../ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { centerLabel } from "../../lib/centerLabel";
import { format } from "../../lib/date";
import { rupeesExact as rupeesText } from "./money";
import { INVOICE_KIND_LABEL } from "./InvoiceDocument";
import { invoicePath } from "./InvoiceLink";
import { SkeletonRows, monthLabel } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

const KIND_OPTIONS = Object.entries(INVOICE_KIND_LABEL);

function periodOf(i: PayrollInvoice): string {
  if (i.kind === "salary" && i.month) return monthLabel(i.month);
  if (i.period_from) return i.period_from === i.period_to ? format(i.period_from) : `${format(i.period_from)} – ${format(i.period_to || i.period_from)}`;
  return "—";
}

/** One captain's invoices (a tab in the pay dialog). */
export function InvoicesTab({ captainId }: { captainId: string }) {
  return <InvoiceList scope={{ captain_id: captainId }} showCaptain={false} showFilters={false} />;
}

/** Every invoice in view — the Invoices section of both Salary pages. The server scopes a manager to their center. */
export function InvoicesSection({ admin }: { admin: boolean }) {
  return <InvoiceList scope={{}} showCaptain showFilters admin={admin} />;
}

function InvoiceList({ scope, showCaptain, showFilters, admin }: { scope: InvoiceListFilters; showCaptain: boolean; showFilters: boolean; admin?: boolean }) {
  const { user } = useAuth();
  const [center, setCenter] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [voiding, setVoiding] = useState<PayrollInvoice | null>(null);
  const filters: InvoiceListFilters = { ...scope, center_id: admin ? center : "", kind, status, from, to, page, page_size: 25 };
  const query = useQuery({ queryKey: ["payroll-invoices", filters], queryFn: () => payrollApi.invoices(filters), placeholderData: keepPreviousData });
  const centers = useQuery({
    queryKey: ["admin-centers-lite"],
    queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100, include_archived: true }),
    enabled: !!admin,
  });
  const rows = query.data?.data || [];
  const pages = query.data?.meta.total_pages || 1;
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(1);
  };

  return (
    <div className="space-y-3">
      {showFilters && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Invoices" value={query.data?.totals.count ?? 0} />
            <StatCard label="Paid Out" value={rupeesText(query.data?.totals.paid_paise)} tone="success" hint="Cancelled invoices not counted" />
          </div>
          <div className="grid grid-cols-2 items-end gap-3 sm:flex sm:flex-wrap">
            {admin && (
              <div className="sm:w-52">
                <Select label="Service Center" value={center} onChange={(e) => reset(setCenter)(e.target.value)}>
                  <option value="">All Centers</option>
                  {(centers.data?.data || []).map((c) => (
                    <option key={c.id} value={c.id}>
                      {centerLabel(c)}
                    </option>
                  ))}
                </Select>
              </div>
            )}
            <div className="sm:w-44">
              <Select label="Type" value={kind} onChange={(e) => reset(setKind)(e.target.value)}>
                <option value="">All</option>
                {KIND_OPTIONS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </div>
            <div className="sm:w-36">
              <Select label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
                <option value="">All</option>
                <option value="paid">Paid</option>
                <option value="void">Cancelled</option>
              </Select>
            </div>
            <div className="sm:w-40">
              <Input type="date" label="Paid From" value={from} onChange={(e) => reset(setFrom)(e.target.value)} />
            </div>
            <div className="sm:w-40">
              <Input type="date" label="Paid To" value={to} onChange={(e) => reset(setTo)(e.target.value)} />
            </div>
          </div>
        </>
      )}

      {query.isLoading ? (
        <SkeletonRows rows={4} />
      ) : query.isError && !query.data ? (
        <ErrorState message="Couldn't load invoices." busy={query.isFetching} onRetry={() => void query.refetch()} />
      ) : !rows.length ? (
        <EmptyState icon={FileText} title="No Invoices Yet" description="Every payment to a captain creates an invoice. Use Pay to make one." />
      ) : (
        <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100">
          {rows.map((i) => (
            <li key={i.id} className={`flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 ${i.status === "void" ? "opacity-60" : ""}`}>
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-[var(--color-text-primary)]">
                  <Link to={invoicePath(user?.role, i.id)} className="font-mono-num underline underline-offset-2">
                    {i.number}
                  </Link>
                  <Badge tone="neutral">{INVOICE_KIND_LABEL[i.kind] || i.kind}</Badge>
                  {i.status === "void" && <Badge tone="error">Cancelled</Badge>}
                  {showCaptain && <span className="text-[var(--color-text-secondary)]">{i.captain_name}</span>}
                </p>
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {periodOf(i)} · Paid {format(i.paid_on)}
                  {admin && i.service_center_name ? ` · ${i.service_center_name}` : ""}
                  {i.adjustment_paise !== 0 ? ` · ${i.adjustment_paise > 0 ? "+" : "−"}${rupeesText(Math.abs(i.adjustment_paise))} Adjusted` : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span className="font-mono-num text-sm font-semibold">{rupeesText(i.paid_paise)}</span>
                <Link to={invoicePath(user?.role, i.id)}>
                  <Button size="sm" variant="outline">
                    Open
                  </Button>
                </Link>
                {i.status === "paid" && (
                  <Button size="sm" variant="ghost" aria-label={`Cancel invoice ${i.number}`} onClick={() => setVoiding(i)}>
                    <Undo2 className="h-3.5 w-3.5" /> Void
                  </Button>
                )}
              </div>
            </li>
          ))}
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
      <VoidInvoiceModal invoice={voiding} onClose={() => setVoiding(null)} />
    </div>
  );
}

/** Cancel an invoice: the payment, its Finance entry and what it settled are all reversed. A reason is required. */
export function VoidInvoiceModal({ invoice, onClose }: { invoice: PayrollInvoice | null; onClose: () => void }) {
  return (
    <Modal open={!!invoice} onClose={onClose} title="Cancel Invoice?">
      {invoice && <VoidForm invoice={invoice} onClose={onClose} />}
    </Modal>
  );
}

function VoidForm({ invoice, onClose }: { invoice: PayrollInvoice; onClose: () => void }) {
  const write = usePayrollWrite();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () => payrollApi.voidInvoice(invoice.id, reason.trim()),
    onSuccess: (res) => {
      write.ok("Invoice Cancelled", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-secondary)]">
        Invoice <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{invoice.number}</span> for {rupeesText(invoice.paid_paise)} will be cancelled. The payment and its Finance entry are reversed,
        the captain's dues go back up, and its bookings can be invoiced again. The invoice stays on record, marked cancelled.
      </p>
      <Input label="Reason" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Paid to the wrong captain" autoFocus />
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" onClick={onClose}>
          Keep It
        </Button>
        <Button variant="danger" className="flex-1" disabled={reason.trim().length < 3} isLoading={mutation.isPending} onClick={() => mutation.mutate()}>
          Cancel Invoice
        </Button>
      </div>
    </div>
  );
}
