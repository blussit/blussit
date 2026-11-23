import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, FileText } from "lucide-react";
import { franchiseApi, type FranchiseCenterRow } from "../../api/franchise";
import { Badge, Button, EmptyState, ErrorState, StatCard } from "../../components/ui";
import { PageLoader } from "../../components/ui/Spinner";
import { FranchiseStatementModal } from "../../components/franchise/FranchiseStatementModal";
import { inr } from "../../components/franchise/money";
import { getErrorMessage } from "../../lib/api-client";
import { toTitle } from "../../lib/titleCase";

/** Net owed on a row: positive = Blussit owes the partner, negative = the partner owes Blussit. */
function NetBadge({ paise }: { paise: number }) {
  if (paise === 0) return <Badge tone="success">Settled</Badge>;
  return <Badge tone={paise > 0 ? "warning" : "info"}>{paise > 0 ? "Payable" : "Receivable"}</Badge>;
}

/**
 * Admin → Franchise: partner-run centers with what each partner has earned,
 * been paid and is still owed (ARCHITECTURE.md §7.13). A row opens the monthly
 * statement, where settlements are recorded.
 */
export default function AdminFranchisePage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["franchise-centers"],
    queryFn: franchiseApi.centers,
    staleTime: 0,
    refetchOnWindowFocus: true,
  });
  const [open, setOpen] = useState<FranchiseCenterRow | null>(null);
  const rows = data?.items ?? [];
  const t = data?.totals;
  const settledPaid = rows.reduce((n, r) => n + r.settled_paid_paise, 0);
  const settledReceived = rows.reduce((n, r) => n + r.settled_received_paise, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Franchise</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Partner-run centers: the partner's share of every completed booking, what has been settled, and what is still owed.
        </p>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : error && !data ? (
        <ErrorState message={getErrorMessage(error)} onRetry={() => void refetch()} busy={isFetching} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="Franchise Centers" icon={Building2} value={rows.length.toLocaleString("en-IN")} hint={`${rows.filter((r) => r.type === "franchise").length} Running As Franchise`} />
            <StatCard label="Share Accrued" value={inr(t?.share_accrued_paise)} hint="Partners' share of completed bookings" />
            <StatCard label="Settled" value={inr(settledPaid)} hint={settledReceived ? `Paid to partners · ${inr(settledReceived)} received back` : "Paid to partners"} />
            <StatCard
              label="Net Payable To Partners"
              value={inr(t?.payable_paise)}
              tone={t && t.payable_paise > 0 ? "error" : "default"}
              hint={t && t.receivable_paise > 0 ? `${inr(t.receivable_paise)} receivable from partners` : "After the cash partners hold"}
            />
          </div>

          {rows.length === 0 ? (
            <EmptyState title="No Franchise Centers Yet" description="Set a service center's Operation to Franchise to see it here." />
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden overflow-x-auto rounded-2xl border border-[var(--color-card-border)] bg-white md:block">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-[var(--color-card-border)]">
                      {["Center", "Partner", "Share", "Accrued", "Settled", "Net", ""].map((h) => (
                        <th key={h} className={`px-4 py-3 text-xs font-medium text-[var(--ui-muted,#5F6878)] ${["Accrued", "Settled", "Net"].includes(h) ? "text-right" : ""}`}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} className="border-b border-[var(--color-card-border)] last:border-0 hover:bg-[var(--ui-tint,#F6F8FC)]">
                        <td className="px-4 py-3">
                          <p className="font-semibold text-[var(--color-text-primary)]">{toTitle(r.name)}</p>
                          <p className="text-xs text-[var(--color-text-secondary)]">
                            {r.code}
                            {r.type !== "franchise" && <Badge className="ml-2" tone="neutral">No Longer Franchise</Badge>}
                          </p>
                        </td>
                        <td className="px-4 py-3">
                          <p>{r.partner?.name ? toTitle(r.partner.name) : "—"}</p>
                          <p className="text-xs text-[var(--color-text-secondary)]">{r.partner?.phone}</p>
                        </td>
                        <td className="font-mono-num px-4 py-3">{r.revenue_share_percent ?? "—"}{r.revenue_share_percent != null && "%"}</td>
                        <td className="font-mono-num px-4 py-3 text-right">{inr(r.share_accrued_paise)}</td>
                        <td className="px-4 py-3 text-right">
                          <p className="font-mono-num">{inr(r.settled_paid_paise)}</p>
                          {r.settled_received_paise > 0 && <p className="text-xs text-[var(--color-text-secondary)]">{inr(r.settled_received_paise)} received</p>}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <p className="font-mono-num font-semibold">{inr(Math.abs(r.net_payable_paise))}</p>
                          <NetBadge paise={r.net_payable_paise} />
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Button size="sm" variant="outline" aria-label={`Open statement for ${r.name}`} onClick={() => setOpen(r)}>
                            <FileText className="h-3.5 w-3.5" /> Statement
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* Phone cards */}
              <ul className="space-y-3 md:hidden">
                {rows.map((r) => (
                  <li key={r.id} className="rounded-2xl border border-[var(--color-card-border)] bg-white p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-[var(--color-text-primary)]">{toTitle(r.name)}</p>
                        <p className="text-xs text-[var(--color-text-secondary)]">{r.partner?.name ? toTitle(r.partner.name) : "No Partner Name"} · {r.revenue_share_percent ?? 0}% Share</p>
                      </div>
                      <NetBadge paise={r.net_payable_paise} />
                    </div>
                    <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                      <div><dt className="text-[var(--color-text-secondary)]">Accrued</dt><dd className="font-mono-num mt-0.5 font-semibold">{inr(r.share_accrued_paise)}</dd></div>
                      <div><dt className="text-[var(--color-text-secondary)]">Settled</dt><dd className="font-mono-num mt-0.5 font-semibold">{inr(r.settled_paid_paise)}</dd></div>
                      <div><dt className="text-[var(--color-text-secondary)]">{r.net_payable_paise < 0 ? "Receivable" : "Payable"}</dt><dd className="font-mono-num mt-0.5 font-semibold">{inr(Math.abs(r.net_payable_paise))}</dd></div>
                    </dl>
                    <Button size="sm" variant="outline" className="mt-3 w-full" onClick={() => setOpen(r)}>
                      <FileText className="h-3.5 w-3.5" /> Statement
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}

      <FranchiseStatementModal center={open} onClose={() => setOpen(null)} />
    </div>
  );
}
