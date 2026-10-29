import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FileUp } from "lucide-react";
import { couponBundleApi, type CouponBundle } from "../../api/couponBundles";
import { CouponTabs } from "../../components/admin/CouponTabs";
import { Pager, SearchBox, useDebouncedValue } from "../../components/shared/ListControls";
import { Badge, Button, DataTable } from "../../components/ui";
import { format, formatDateTime } from "../../lib/date";

export const stateTone = (state: CouponBundle["state"]) => (state === "Active" ? "success" : state === "Expired" ? "neutral" : "error");

const USE_LABELS: Record<string, string> = { services: "Bookings", plans: "Plans", society: "Society", custom: "Custom Plans" };
export const appliesText = (uses?: string[]) => (uses?.length ? uses.map((u) => USE_LABELS[u] ?? u).join(", ") : "—");

export function benefitText(b: Pick<CouponBundle, "offer_kind" | "coupon_type" | "value" | "max_discount_amount">): string {
  if (b.offer_kind === "free_addon_with_service") return "Free Add-On";
  if (b.coupon_type === "percentage") return `${b.value}% Off${b.max_discount_amount ? ` (Max ₹${b.max_discount_amount})` : ""}`;
  return `₹${b.value} Off`;
}

export default function AdminCouponBundlesPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim());
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin-coupon-bundles", page, q],
    queryFn: () => couponBundleApi.list({ page, page_size: 20, search: q }),
    placeholderData: keepPreviousData,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Coupons</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Single-use codes imported in bulk, managed as one bundle.</p>
        </div>
        <Button onClick={() => navigate("/admin/coupons/bundles/new")}>
          <FileUp className="h-4 w-4" /> Import Bundle
        </Button>
      </div>
      <CouponTabs />

      <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search Bundle Name" className="max-w-sm" />

      <DataTable<CouponBundle>
        isLoading={isLoading}
        data={data?.data || []}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle="No Coupon Bundles Yet"
        emptyDescription="Import a CSV of codes to create your first bundle."
        onRowClick={(b) => navigate(`/admin/coupons/bundles/${b.id}`)}
        columns={[
          { header: "Bundle", accessor: (b) => <span className="font-semibold">{b.name}</span> },
          { header: "Offer", accessor: (b) => benefitText(b) },
          { header: "Applies To", accessor: (b) => appliesText(b.applies_to) },
          { header: "Created", accessor: (b) => format(b.created_at) },
          { header: "Expiry", accessor: (b) => (b.valid_until ? formatDateTime(b.valid_until) : "No Expiry") },
          { header: "Uses Per Coupon", accessor: (b) => <span className="font-mono-num">{b.uses_per_coupon}</span> },
          { header: "Total", accessor: (b) => <span className="font-mono-num">{b.counts.total.toLocaleString("en-IN")}</span> },
          { header: "Unused", accessor: (b) => <span className="font-mono-num">{b.counts.unused.toLocaleString("en-IN")}</span> },
          { header: "Used", accessor: (b) => <span className="font-mono-num">{(b.counts.used + b.counts.partly_used).toLocaleString("en-IN")}</span> },
          { header: "Expired / Disabled", accessor: (b) => <span className="font-mono-num">{(b.counts.expired + b.counts.disabled).toLocaleString("en-IN")}</span> },
          { header: "Status", accessor: (b) => <Badge tone={stateTone(b.state)}>{b.state}</Badge> },
        ]}
      />
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}
    </div>
  );
}
