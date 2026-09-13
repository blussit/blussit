import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Phone } from "lucide-react";
import { adminPlanEnquiryApi } from "../../api/admin";
import { Badge, Button, DataTable, Select } from "../../components/ui";
import { formatDateTime } from "../../lib/date";
import type { PlanEnquiry } from "../../types";

const STATUS_TONE: Record<PlanEnquiry["status"], "warning" | "info" | "neutral"> = {
  new: "warning",
  contacted: "info",
  closed: "neutral",
};

export default function AdminPlanEnquiriesPage() {
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-plan-enquiries", page],
    queryFn: () => adminPlanEnquiryApi.list({ page, page_size: 20 }),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: PlanEnquiry["status"] }) => adminPlanEnquiryApi.setStatus(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-plan-enquiries"] }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Custom plan requests</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          "Request A Custom Plan" submissions from the Monthly Pass section — a fleet, an odd schedule, anything the standard pass doesn't fit.
        </p>
      </div>

      <DataTable<PlanEnquiry>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No requests yet"
        emptyDescription="Custom-plan requests from the website will show up here."
        columns={[
          { header: "Name", accessor: (e) => e.name },
          {
            header: "Contact",
            accessor: (e) => (
              <a href={`tel:${e.phone}`} className="flex items-center gap-1 text-xs font-medium text-[var(--color-primary)] hover:underline">
                <Phone className="h-3 w-3" /> {e.phone}
              </a>
            ),
          },
          {
            header: "What they need",
            accessor: (e) => (
              <div className="max-w-sm text-xs">
                <p className="font-medium text-[var(--color-text-primary)]">
                  {e.vehicle_count} vehicle{e.vehicle_count === 1 ? "" : "s"} · {e.services_wanted}
                </p>
                {e.washes_per_month != null && <p className="text-[var(--color-text-secondary)]">{e.washes_per_month} washes/month</p>}
                {e.preferred_time && <p className="text-[var(--color-text-secondary)]">Preferred: {e.preferred_time}</p>}
                {e.notes && <p className="mt-0.5 text-[var(--color-text-secondary)]">"{e.notes}"</p>}
              </div>
            ),
          },
          {
            header: "Asked",
            accessor: (e) => (
              <div className="text-xs">
                <p>{formatDateTime(e.last_requested_at || e.created_at)}</p>
                {!!e.requests_count && e.requests_count > 1 && <p className="text-[var(--color-text-secondary)]">Asked {e.requests_count}×</p>}
              </div>
            ),
          },
          {
            header: "Status",
            accessor: (e) => (
              <div className="flex items-center gap-2">
                <Badge tone={STATUS_TONE[e.status]}>{e.status}</Badge>
                <Select
                  className="!w-auto !py-1 text-xs"
                  value={e.status}
                  onChange={(ev) => statusMutation.mutate({ id: e.id, status: ev.target.value as PlanEnquiry["status"] })}
                >
                  <option value="new">New</option>
                  <option value="contacted">Contacted</option>
                  <option value="closed">Closed</option>
                </Select>
              </div>
            ),
          },
        ]}
      />

      {data && data.meta.total_pages > 1 && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <Button size="sm" variant="outline" disabled={page >= data.meta.total_pages} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
