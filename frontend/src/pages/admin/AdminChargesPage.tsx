import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { adminServiceCenterApi } from "../../api/admin";
import { ChargesList } from "../../components/shared/CancellationCharges";
import { PaybacksList } from "../../components/shared/CustomerWalletPanel";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";

const TABS = [
  { key: "charges", label: "Charges" },
  { key: "payouts", label: "Paybacks" },
] as const;

/** Every late-cancellation charge across centers (with its full history),
 *  and every payback managers recorded (MONEY-2). */
export default function AdminChargesPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "payouts" ? "payouts" : "charges";
  const [customerId, setCustomerId] = useState<string | null>(null);
  const { data: centers } = useQuery({ queryKey: ["service-centers-all"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const centerNames = useMemo(() => Object.fromEntries((centers?.data ?? []).map((c) => [c.id, c.name])), [centers]);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">{tab === "payouts" ? "Paybacks" : "Cancellation Charges"}</h1>
          <p className="mt-1 max-w-3xl text-sm text-[var(--color-text-secondary)]">
            {tab === "payouts"
              ? "Money managers paid back for cancelled, delayed or problem bookings — from the wallet first, the rest paid by the manager."
              : "Late cancellations across every center — charged to the customer's wallet, and what was reduced or waived, by whom."}
          </p>
        </div>
        <div className="flex max-w-full gap-1 overflow-x-auto rounded-xl border border-[var(--color-card-border)] bg-white p-1" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              onClick={() => setParams(t.key === "charges" ? {} : { tab: t.key })}
              className={`min-h-11 shrink-0 whitespace-nowrap rounded-lg px-3.5 text-sm font-semibold transition-colors sm:min-h-9 ${
                tab === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      {tab === "payouts" ? <PaybacksList onOpenCustomer={setCustomerId} /> : <ChargesList centerNames={centerNames} />}
      <CustomerDetailDrawer customerId={customerId} onClose={() => setCustomerId(null)} />
    </div>
  );
}
