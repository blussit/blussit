import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { adminServiceCenterApi } from "../../api/admin";
import { Button, Select } from "../../components/ui";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { CustomPlanList, RenewLoader, ReviseLoader, SavedPlan } from "../manager/ManagerCustomPlansPage";

/** Every custom multi-car plan managers built, across centers: status,
 *  per-car washes left, the open link, and any refund due. Admin builds no
 *  new carts here, but can Renew Plan / Refund Car on a paid one (the same
 *  builder and dialog managers use) and finish a renewal it started —
 *  send its link, take cash, revise or cancel it. */
export default function AdminCustomPlansPage() {
  const [params, setParams] = useSearchParams();
  const [centerId, setCenterId] = useState("");
  const [customerId, setCustomerId] = useState<string | null>(null);
  const { data: centers } = useQuery({ queryKey: ["service-centers-all"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const centerNames = useMemo(() => Object.fromEntries((centers?.data ?? []).map((c) => [c.id, c.name])), [centers]);
  const renewId = params.get("renew");
  const reviseId = params.get("revise");
  const planId = params.get("plan");
  const go = (next: Record<string, string>) => setParams(next);
  const toList = () => go({});
  const working = !!(renewId || reviseId || planId);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{renewId ? "Renew Plan" : reviseId ? "Revise Plan" : "Custom Plans"}</h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-text-secondary)]">
            {working ? "Same builder and actions a manager uses." : "Multi-car plans managers sold — what each car has left, links waiting, and refunds due."}
          </p>
        </div>
        {working ? (
          <Button variant="outline" className="min-h-11" onClick={toList}>
            <ArrowLeft className="h-4 w-4" /> All Plans
          </Button>
        ) : (
          <div className="w-full sm:w-64">
            <Select label="Center" value={centerId} onChange={(e) => setCenterId(e.target.value)}>
              <option value="">All Centers</option>
              {(centers?.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
        )}
      </div>
      {renewId ? (
        <RenewLoader key={renewId} planId={renewId} onSaved={(p) => go({ plan: p.id })} onCancel={() => go({ plan: renewId })} />
      ) : reviseId ? (
        <ReviseLoader key={reviseId} planId={reviseId} onSaved={(p) => go({ plan: p.id })} onCancel={toList} />
      ) : planId ? (
        <SavedPlan
          key={planId}
          planId={planId}
          adminActions
          onOpenCustomer={setCustomerId}
          onRevise={(p) => go({ revise: p.id })}
          onRenew={(p) => go({ renew: p.id })}
          onOpenPlan={(id) => go({ plan: id })}
          onAll={toList}
        />
      ) : (
        <CustomPlanList
          readOnly
          adminActions
          centerId={centerId || undefined}
          centerNames={centerNames}
          onOpenCustomer={setCustomerId}
          onRevise={(p) => go({ revise: p.id })}
          onRenew={(p) => go({ renew: p.id })}
          onOpenPlan={(id) => go({ plan: id })}
        />
      )}
      <CustomerDetailDrawer customerId={customerId} onClose={() => setCustomerId(null)} />
    </div>
  );
}
