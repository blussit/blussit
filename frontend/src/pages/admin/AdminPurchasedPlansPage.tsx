import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { Badge, DataTable, Input, Panel, StatCard, type Column } from "../../components/ui";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { PlanUsageModal } from "../../components/shared/PlanUsageModal";
import { format } from "../../lib/date";

type PlanFilter = "all" | "active" | "expired";

/**
 * Every plan ever purchased or granted, platform-wide — who bought what,
 * for how much, and how (online/cash/free), whichever center sold it.
 * Read-only: a subscription's lifecycle is managed from the customer's own
 * account or the manager's Subscriptions page, not from here.
 */
export default function AdminPurchasedPlansPage() {
  const [filter, setFilter] = useState<PlanFilter>("all");
  const [planFilter, setPlanFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [detailCustomerId, setDetailCustomerId] = useState<string | null>(null);
  const [usageSubscriptionId, setUsageSubscriptionId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["admin-subscriptions-overview"],
    queryFn: () => subscriptionApi.adminOverview(),
  });

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.rows || [])
      .filter((r) => filter === "all" || r.status === filter)
      .filter((r) => !planFilter || r.plan_name === planFilter)
      .filter(
        (r) =>
          !q ||
          r.customer_name.toLowerCase().includes(q) ||
          (r.customer_phone || "").includes(q) ||
          r.plan_name.toLowerCase().includes(q)
      )
      .map((r) => ({ ...r, id: r.subscription_id }));
  }, [data, filter, planFilter, search]);

  const kpis = data?.kpis;

  const columns: Column<(typeof rows)[number]>[] = [
    {
      header: "Customer",
      accessor: (r) => (
        <div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setDetailCustomerId(r.customer_id);
            }}
            className="font-medium text-black underline decoration-[#F3E5B5] decoration-2 underline-offset-2 hover:decoration-black"
          >
            {r.customer_name}
          </button>
          {r.customer_phone && <p className="text-xs text-gray-400">{r.customer_phone}</p>}
        </div>
      ),
    },
    {
      header: "Plan",
      accessor: (r) => (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setUsageSubscriptionId(r.subscription_id);
          }}
          className="text-black underline decoration-[#F3E5B5] decoration-2 underline-offset-2 hover:decoration-black"
        >
          {r.plan_name}
        </button>
      ),
    },
    {
      header: "Status",
      accessor: (r) => (
        <div>
          <Badge tone={r.status === "active" ? "success" : r.status === "expired" ? "warning" : "neutral"}>{r.status || "—"}</Badge>
          {r.total_service_count != null && (
            <p className="mt-1 text-xs text-gray-400">
              {r.remaining_service_count}/{r.total_service_count} washes left
            </p>
          )}
        </div>
      ),
    },
    {
      header: "Paid",
      accessor: (r) => (
        <div className="font-mono-num">
          {r.amount_paid != null ? `₹${r.amount_paid}` : "—"}
          {r.discount_amount ? <span className="ml-1 text-xs text-gray-400">(−₹{r.discount_amount})</span> : null}
        </div>
      ),
    },
    {
      header: "Method",
      accessor: (r) => (
        <span className="capitalize">
          {r.payment_method || (r.service_center_id ? "free grant" : "—")}
          {r.coupon_code ? <span className="ml-1 text-xs text-gray-400">· {r.coupon_code}</span> : null}
          {r.auto_renew ? <span className="ml-1 text-xs text-gray-400">· auto-pay</span> : null}
        </span>
      ),
    },
    { header: "Sold by", accessor: (r) => r.service_center_name || <span className="text-gray-400">self-serve</span> },
    { header: "Bought", accessor: (r) => (r.start_date ? format(r.start_date) : "—") },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Purchased plans</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Every subscription bought or granted across every center — who holds what, and what it brought in.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Total plans" value={kpis?.total ?? "—"} />
        <StatCard label="Active" value={kpis?.active ?? "—"} tone="success" />
        <StatCard label="Expired" value={kpis?.expired ?? "—"} tone="muted" />
        <StatCard label="Total revenue" value={kpis ? `₹${kpis.total_revenue.toLocaleString()}` : "—"} tone="success" />
      </div>

      <Panel>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1.5">
            {(["all", "active", "expired"] as PlanFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded-full border px-3 py-1 text-xs font-medium capitalize ${
                  filter === f ? "border-2 border-black bg-[var(--color-primary-light)]" : "border-gray-200 text-gray-500 hover:border-gray-300"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div className="relative ml-auto max-w-xs flex-1">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input className="pl-10" placeholder="Search name, phone, or plan…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>

        {!!data?.plan_breakdown.length && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">By plan:</span>
            {data.plan_breakdown.map((p) => (
              <button
                key={p.plan_name}
                onClick={() => setPlanFilter((prev) => (prev === p.plan_name ? null : p.plan_name))}
                className={`rounded-full border px-3 py-1 text-xs font-medium ${
                  planFilter === p.plan_name ? "border-2 border-black bg-[var(--color-primary-light)]" : "border-gray-200 text-gray-500 hover:border-gray-300"
                }`}
              >
                {p.plan_name} · {p.count}
              </button>
            ))}
          </div>
        )}

        <div className="mt-4">
          <DataTable columns={columns} data={rows} isLoading={isLoading} emptyTitle="No plans match" />
        </div>
      </Panel>

      <CustomerDetailDrawer customerId={detailCustomerId} onClose={() => setDetailCustomerId(null)} />
      <PlanUsageModal subscriptionId={usageSubscriptionId} onClose={() => setUsageSubscriptionId(null)} />
    </div>
  );
}
