import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Printer, Undo2 } from "lucide-react";
import { Button, ErrorState } from "../ui";
import { payrollApi } from "../../api/payroll";
import { useAuth } from "../../context/AuthContext";
import { InvoiceDocument } from "./InvoiceDocument";
import { SkeletonRows } from "./payrollUi";
import { VoidInvoiceModal } from "./InvoicesTab";

const BACK: Record<string, string> = { manager: "/manager/salary?view=invoices", admin: "/admin/captain-salary?view=invoices", captain: "/captain/earnings" };

/** One invoice as a printable page: Print / Save As PDF uses the browser's own print. */
export default function InvoicePage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const [voiding, setVoiding] = useState(false);
  const query = useQuery({ queryKey: ["payroll-invoice", id], queryFn: () => payrollApi.invoice(id) });
  const inv = query.data;
  const canVoid = (user?.role === "manager" || user?.role === "admin") && inv?.status === "paid";

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div className="no-print mx-auto flex w-full max-w-3xl flex-wrap items-center justify-between gap-2">
        <Link to={BACK[user?.role || "manager"] || "/"} className="inline-flex items-center gap-1.5 text-sm font-semibold text-[#0A66F0] hover:underline">
          <ArrowLeft className="h-4 w-4" /> Back
        </Link>
        <div className="flex gap-2">
          {canVoid && (
            <Button size="sm" variant="outline" onClick={() => setVoiding(true)}>
              <Undo2 className="h-4 w-4" /> Cancel Invoice
            </Button>
          )}
          <Button size="sm" disabled={!inv} onClick={() => window.print()}>
            <Printer className="h-4 w-4" /> Print / Save As PDF
          </Button>
        </div>
      </div>
      {query.isLoading ? (
        <SkeletonRows rows={6} />
      ) : query.isError || !inv ? (
        <ErrorState message="Couldn't open this invoice." busy={query.isFetching} onRetry={() => void query.refetch()} />
      ) : (
        <InvoiceDocument invoice={inv} />
      )}
      {inv && <VoidInvoiceModal invoice={voiding ? inv : null} onClose={() => setVoiding(false)} />}
    </div>
  );
}
