import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlarmClock, BadgeCheck, CircleOff, Gift, Phone, Search } from "lucide-react";
import { subscriptionApi, type CenterSubscriptionRow } from "../../api/engagement";
import { useAuth } from "../../context/AuthContext";
import { Badge, Card, EmptyState, Input, PageLoader } from "../../components/ui";
import { format } from "../../lib/date";

type Filter = "all" | "active" | "expiring" | "expired";

const FILTER_EMPTY: Record<Filter, string> = {
  all: "No customer of this center holds a plan yet — assign one from New booking, or pitch a plan at the door.",
  active: "No active plans right now.",
  expiring: "Nothing expires in the next 14 days.",
  expired: "No expired plans.",
};

function rowMatches(r: CenterSubscriptionRow, filter: Filter): boolean {
  if (filter === "active") return r.status === "active";
  if (filter === "expiring") return r.status === "active" && r.days_left != null && r.days_left <= 14;
  if (filter === "expired") return r.status === "expired";
  return true;
}

export default function ManagerSubscribersPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const [filter, setFilter] = useState<Filter>("all");
  const [planFilter, setPlanFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["center-subscription-overview", centerId],
    queryFn: () => subscriptionApi.centerOverview(centerId),
    enabled: !!centerId,
  });

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.rows || [])
      .filter((r) => rowMatches(r, filter))
      .filter((r) => !planFilter || r.plan_name === planFilter)
      .filter((r) => !q || r.customer_name.toLowerCase().includes(q) || (r.customer_phone || "").includes(q) || r.plan_name.toLowerCase().includes(q));
  }, [data, filter, planFilter, search]);

  const kpis = data?.kpis;
  const tiles: { key: Filter; label: string; value: number | string; icon: typeof Gift; accent?: string }[] = [
    { key: "all", label: "Plans held", value: kpis?.total ?? "—", icon: Gift },
    { key: "active", label: "Active", value: kpis?.active ?? "—", icon: BadgeCheck, accent: "text-green-600" },
    { key: "expiring", label: "Expiring in 14 days", value: kpis?.expiring_soon ?? "—", icon: AlarmClock, accent: "text-amber-600" },
    { key: "expired", label: "Expired", value: kpis?.expired ?? "—", icon: CircleOff, accent: "text-gray-400" },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Subscriptions</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Every plan held by a customer this center serves — who's covered, what they bought, and who to call before it lapses.
        </p>
      </div>

      {/* Clickable KPI tiles — each one IS the filter for the list below. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((t) => (
          <button
            key={t.key}
            onClick={() => setFilter(t.key)}
            className={`rounded-2xl border bg-white p-4 text-left transition-colors ${
              filter === t.key ? "border-2 border-black bg-[var(--color-primary-light)]" : "border-gray-100 hover:border-gray-300"
            }`}
          >
            <t.icon className={`h-4 w-4 ${t.accent || "text-[var(--color-text-secondary)]"}`} />
            <p className="mt-2 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">{t.value}</p>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{t.label}</p>
          </button>
        ))}
      </div>

      {/* Which plan types are actually selling here. */}
      {!!data?.plan_breakdown.length && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Active by plan:</span>
          {data.plan_breakdown.map((p) => (
            <button
              key={p.plan_name}
              onClick={() => setPlanFilter((prev) => (prev === p.plan_name ? null : p.plan_name))}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                planFilter === p.plan_name ? "border-2 border-black bg-[var(--color-primary-light)]" : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
              }`}
            >
              {p.plan_name} · {p.active_count}
            </button>
          ))}
        </div>
      )}

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <Input className="pl-10" placeholder="Search name, phone, or plan…" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !rows.length ? (
        <EmptyState icon={Gift} title="Nothing here" description={FILTER_EMPTY[filter]} />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {rows.map((r) => {
            const expiringSoon = r.status === "active" && r.days_left != null && r.days_left <= 14;
            const usedCount =
              r.total_service_count != null && r.remaining_service_count != null ? r.total_service_count - r.remaining_service_count : null;
            const pct =
              r.total_service_count && r.remaining_service_count != null
                ? Math.round((r.remaining_service_count / r.total_service_count) * 100)
                : null;
            return (
              <Card key={r.subscription_id} className={`p-4 ${expiringSoon ? "border-amber-200" : ""}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-[var(--color-text-primary)]">{r.customer_name}</p>
                    <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
                      {r.plan_name}
                      {r.vehicle_type_name ? ` · ${r.vehicle_type_name} tier` : ""}
                      {r.purchased_price != null ? ` · ₹${r.purchased_price}` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {r.status && (
                      <Badge tone={r.status === "active" ? "success" : r.status === "expired" ? "warning" : "neutral"}>{r.status}</Badge>
                    )}
                    {r.customer_phone && (
                      <a
                        href={`tel:${r.customer_phone}`}
                        className="flex h-8 w-8 items-center justify-center rounded-full border border-gray-200 text-[var(--color-text-secondary)] hover:border-black hover:text-black"
                        title={`Call ${r.customer_name}`}
                      >
                        <Phone className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </div>
                </div>

                {pct != null && (
                  <div className="mt-3">
                    <div className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
                      <span>
                        {r.remaining_service_count}/{r.total_service_count} washes left
                        {usedCount != null && usedCount > 0 ? ` · ${usedCount} used` : ""}
                      </span>
                      {r.end_date && (
                        <span className={expiringSoon ? "font-semibold text-amber-600" : ""}>
                          {r.status === "active" && r.days_left != null
                            ? r.days_left <= 0
                              ? "expires today"
                              : `${r.days_left} day${r.days_left === 1 ? "" : "s"} left`
                            : `ended ${format(r.end_date)}`}
                        </span>
                      )}
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-gray-100">
                      <div
                        className={`h-full rounded-full ${expiringSoon ? "bg-amber-400" : "bg-black"}`}
                        style={{ width: `${Math.max(pct, 4)}%` }}
                      />
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
