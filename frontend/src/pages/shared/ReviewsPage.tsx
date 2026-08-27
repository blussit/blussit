import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, Star } from "lucide-react";
import { reviewApi } from "../../api/engagement";
import { adminServiceCenterApi, adminUserApi, staffDirectoryApi } from "../../api/admin";
import { vehicleTypeApi } from "../../api/catalog";
import { bookingApi } from "../../api/booking";
import { Badge, DataTable, PageLoader, Select } from "../../components/ui";
import { BookingDetailDrawer } from "../../components/shared/BookingDetailDrawer";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";
import type { Review } from "../../types";

/**
 * Section 12 of the BLUSSIT UX update — one shared Reviews view for both
 * admin (every service center, full filter set) and manager (own center
 * only, backend-enforced via reviewApi.forCenter). Drill-down into the
 * originating booking reuses the same BookingDetailDrawer every other
 * booking list in the app opens, rather than a bespoke review-detail page.
 */
export default function ReviewsPage({ role }: { role: "admin" | "manager" }) {
  const { user } = useAuth();
  const ownCenterId = user?.service_center_id || "";

  const [centerFilter, setCenterFilter] = useState("");
  const [captainFilter, setCaptainFilter] = useState("");
  const [vehicleTypeFilter, setVehicleTypeFilter] = useState("");
  const [ratingFilter, setRatingFilter] = useState("");
  const [search, setSearch] = useState("");
  const [selectedBookingId, setSelectedBookingId] = useState<string | null>(null);

  const { data: centers } = useQuery({ queryKey: ["admin-centers-for-reviews"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }), enabled: role === "admin" });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

  const effectiveCenterId = role === "admin" ? centerFilter : ownCenterId;
  const { data: captains } = useQuery({
    queryKey: ["captains-for-reviews", role, effectiveCenterId],
    queryFn: () => (role === "admin" ? adminUserApi.list({ role: "captain", page: 1, page_size: 100 }) : staffDirectoryApi.captainsForCenter(ownCenterId, { page: 1, page_size: 100 })),
    enabled: role === "admin" || !!ownCenterId,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["reviews-list", role, ownCenterId],
    queryFn: () => (role === "admin" ? reviewApi.forAdmin({ page: 1, page_size: 100 }) : reviewApi.forCenter(ownCenterId, { page: 1, page_size: 100 })),
    enabled: role === "admin" || !!ownCenterId,
  });

  const { data: selectedBooking } = useQuery({
    queryKey: ["review-drilldown-booking", selectedBookingId],
    queryFn: () => bookingApi.get(selectedBookingId!),
    enabled: !!selectedBookingId,
  });

  const filtered = useMemo(() => {
    let items = data?.data || [];
    if (centerFilter) items = items.filter((r) => r.service_center_id === centerFilter);
    if (captainFilter) items = items.filter((r) => r.captain_id === captainFilter);
    if (vehicleTypeFilter) items = items.filter((r) => r.vehicle_type_id === vehicleTypeFilter);
    if (ratingFilter) {
      const min = Number(ratingFilter);
      items = items.filter((r) => (r.captain_rating ?? r.rating ?? 0) >= min || (r.service_rating ?? r.rating ?? 0) >= min);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      items = items.filter(
        (r) =>
          r.booking_number?.toLowerCase().includes(q) ||
          r.customer_name?.toLowerCase().includes(q) ||
          r.captain_name?.toLowerCase().includes(q) ||
          r.captain_comment?.toLowerCase().includes(q) ||
          r.service_comment?.toLowerCase().includes(q)
      );
    }
    return items;
  }, [data, centerFilter, captainFilter, vehicleTypeFilter, ratingFilter, search]);

  if (isLoading) return <PageLoader />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Reviews</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          {role === "admin" ? "Customer feedback across every service center." : "Customer feedback for your service center."}
        </p>
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            className="w-full rounded-xl border border-gray-300 py-2.5 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            placeholder="Booking #, customer, captain, comment…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {role === "admin" && (
          <div className="w-52">
            <Select value={centerFilter} onChange={(e) => setCenterFilter(e.target.value)}>
              <option value="">All service centers</option>
              {(centers?.data || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
        )}
        <div className="w-48">
          <Select value={captainFilter} onChange={(e) => setCaptainFilter(e.target.value)}>
            <option value="">All captains</option>
            {(captains?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.full_name}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-44">
          <Select value={vehicleTypeFilter} onChange={(e) => setVehicleTypeFilter(e.target.value)}>
            <option value="">All vehicle types</option>
            {(vehicleTypes || []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="w-40">
          <Select value={ratingFilter} onChange={(e) => setRatingFilter(e.target.value)}>
            <option value="">Any rating</option>
            <option value="5">5 stars</option>
            <option value="4">4+ stars</option>
            <option value="3">3+ stars</option>
            <option value="2">2+ stars</option>
            <option value="1">1+ stars</option>
          </Select>
        </div>
      </div>

      <DataTable<Review>
        data={filtered}
        emptyTitle="No reviews match these filters"
        onRowClick={(r) => setSelectedBookingId(r.booking_id)}
        columns={[
          { header: "Booking", accessor: (r) => <span className="font-mono-num">{r.booking_number || "—"}</span> },
          { header: "Customer", accessor: (r) => r.customer_name || "—" },
          { header: "Captain", accessor: (r) => r.captain_name || "—" },
          { header: "Service", accessor: (r) => r.combo_name || r.service_names?.join(", ") || "—" },
          ...(role === "admin" ? [{ header: "Center", accessor: (r: Review) => r.service_center_name || "—" }] : []),
          { header: "Captain rating", accessor: (r) => <RatingCell value={r.captain_rating ?? r.rating} /> },
          { header: "Service rating", accessor: (r) => <RatingCell value={r.service_rating ?? r.rating} /> },
          { header: "Date", accessor: (r) => format(r.created_at) },
          { header: "Status", accessor: (r) => (r.is_deleted ? <Badge tone="neutral">Removed by customer</Badge> : <Badge tone="success">Visible</Badge>) },
        ]}
      />

      <BookingDetailDrawer booking={selectedBooking || null} onClose={() => setSelectedBookingId(null)} />
    </div>
  );
}

function RatingCell({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-[var(--color-text-secondary)]">—</span>;
  return (
    <span className="flex items-center gap-1">
      <Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />
      <span className="font-mono-num">{value.toFixed(1)}</span>
    </span>
  );
}
