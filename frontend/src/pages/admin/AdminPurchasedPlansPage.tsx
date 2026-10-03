import { useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { Badge, DataTable, Input, Panel, StatCard, type Column } from "../../components/ui";
import { CustomerDetailDrawer } from "../../components/shared/CustomerDetailDrawer";
import { normalisePhoneSearch, Pager, useDebouncedValue } from "../../components/shared/ListControls";
import { PlanUsageModal } from "../../components/shared/PlanUsageModal";
import { format } from "../../lib/date";
import { carAndService, toTitle } from "../../lib/titleCase";

type PlanFilter = "all" | "active" | "expired";
const PAGE_SIZE = 25;

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

  const [page, setPage] = useState(1);
  const debouncedSearch = useDebouncedValue(normalisePhoneSearch(search), 300);

  // Filters and search run on the server over every subscription; only
  // one page of rows is ever loaded.
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin-subscriptions-overview", filter, planFilter, debouncedSearch, page],
    queryFn: () =>
      subscriptionApi.adminOverview({
        page,
        page_size: PAGE_SIZE,
        status: filter === "all" ? "" : filter,
        plan_id: planFilter || undefined,
        search: debouncedSearch || undefined,
      }),
    placeholderData: keepPreviousData,
  });

  const rows = useMemo(() => (data?.rows || []).map((r) => ({ ...r, id: r.subscription_id })), [data]);
  const planIdByName = useMemo(() => new Map((data?.plans || []).map((p) => [p.plan_name, p.plan_id])), [data]);

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
        <div>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setUsageSubscriptionId(r.subscription_id);
            }}
            className="text-black underline decoration-[#F3E5B5] decoration-2 underline-offset-2 hover:decoration-black"
          >
            {toTitle(r.plan_name)}
          </button>
          {carAndService(r.vehicle_type_name, r.service_name) && (
            <p className="mt-0.5 text-xs text-gray-500">{carAndService(r.vehicle_type_name, r.service_name)}</p>
          )}
        </div>
      ),
    },
    {
      header: "Status",
      accessor: (r) => (
        <div>
          <Badge tone={r.status === "active" ? "success" : r.status === "expired" ? "warning" : "neutral"}>{toTitle(r.status) || "—"}</Badge>
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
        <span>
          {toTitle(r.payment_method) || (r.service_center_id ? "Free Grant" : "—")}
          {r.coupon_code ? <span className="ml-1 text-xs text-gray-400">· {r.coupon_code}</span> : null}
          {r.auto_renew ? <span className="ml-1 text-xs text-gray-400">· Auto-Pay</span> : null}
        </span>
      ),
    },
    { header: "Sold By", accessor: (r) => r.service_center_name || <span className="text-gray-400">Self-Serve</span> },
    { header: "Bought", accessor: (r) => (r.start_date ? format(r.start_date) : "—") },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Purchased Plans</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Every subscription bought or granted across every center — who holds what, and what it brought in.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Total Plans" value={kpis?.total ?? "—"} />
        <StatCard label="Active" value={kpis?.active ?? "—"} tone="success" />
        <StatCard label="Expired" value={kpis?.expired ?? "—"} tone="muted" />
        <StatCard label="Total Revenue" value={kpis ? `₹${kpis.total_revenue.toLocaleString()}` : "—"} tone="success" />
      </div>

      <Panel>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1.5">
            {(["all", "active", "expired"] as PlanFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => {
                  setFilter(f);
                  setPage(1);
                }}
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
            <Input
              className="pl-10"
              placeholder="Search name, phone, or plan…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
        </div>

        {!!data?.plan_breakdown.length && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-gray-500">By Plan:</span>
            {data.plan_breakdown.map((p) => {
              const planId = planIdByName.get(p.plan_name);
              return (
                <button
                  key={p.plan_name}
                  disabled={!planId}
                  onClick={() => {
                    setPlanFilter((prev) => (prev === planId ? null : planId || null));
                    setPage(1);
                  }}
                  className={`rounded-full border px-3 py-1 text-xs font-medium disabled:cursor-default ${
                    planId && planFilter === planId ? "border-2 border-black bg-[var(--color-primary-light)]" : "border-gray-200 text-gray-500 hover:border-gray-300"
                  }`}
                >
                  {toTitle(p.plan_name)} · {p.count}
                </button>
              );
            })}
          </div>
        )}

        <div className="mt-4">
          <DataTable columns={columns} data={rows} isLoading={isLoading} emptyTitle="No Plans Match" />
        </div>
        {data?.meta && (
          <div className="mt-4">
            <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />
          </div>
        )}
      </Panel>

      <CustomerDetailDrawer customerId={detailCustomerId} onClose={() => setDetailCustomerId(null)} />
      <PlanUsageModal subscriptionId={usageSubscriptionId} onClose={() => setUsageSubscriptionId(null)} />
    </div>
  );
}
