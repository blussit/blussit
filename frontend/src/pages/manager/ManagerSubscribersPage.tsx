import { useQuery } from "@tanstack/react-query";
import { bookingApi } from "../../api/booking";
import { useAuth } from "../../context/AuthContext";
import { Badge, DataTable } from "../../components/ui";
import { format } from "../../lib/date";

interface SubscriberRow {
  id: string;
  customer_id: string;
  customer_name: string;
  customer_phone?: string;
  plan_name: string;
  subscription_status?: string;
  remaining_service_count?: number;
  last_visit: string;
  visits: number;
}

export default function ManagerSubscribersPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";

  const { data, isLoading } = useQuery({
    queryKey: ["center-subscribers", centerId],
    queryFn: async () => {
      const rows = await bookingApi.subscribersForCenter(centerId);
      return rows.map((r) => ({ ...r, id: r.customer_id }));
    },
    enabled: !!centerId,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Subscribers</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          Customers who've used a subscription plan at your center, and how much they have left.
        </p>
      </div>

      <DataTable<SubscriberRow>
        isLoading={isLoading}
        data={data || []}
        emptyTitle="No subscribers yet"
        emptyDescription="Once a customer books here using a plan, they'll show up in this list."
        columns={[
          { header: "Customer", accessor: (r) => r.customer_name },
          { header: "Phone", accessor: (r) => r.customer_phone || "—" },
          { header: "Plan", accessor: (r) => r.plan_name },
          {
            header: "Status",
            accessor: (r) => (r.subscription_status ? <Badge tone={r.subscription_status === "active" ? "success" : "neutral"}>{r.subscription_status}</Badge> : "—"),
          },
          { header: "Remaining", accessor: (r) => (r.remaining_service_count != null ? r.remaining_service_count : "—") },
          { header: "Visits here", accessor: (r) => r.visits },
          { header: "Last visit", accessor: (r) => format(r.last_visit) },
        ]}
      />
    </div>
  );
}
