import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { adminServiceCenterApi } from "../../api/admin";
import { Badge, DataTable, Select, StatusBadge } from "../../components/ui";
import { ComplaintDetailDrawer } from "../../components/shared/ComplaintDetailDrawer";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = { urgent: "error", high: "error", medium: "warning", low: "neutral" };

/**
 * Platform-wide complaint oversight — every complaint, from every service
 * center, filterable by center and status, in one place (not split into a
 * separate page per center). Clicking a row opens the same full detail +
 * reply thread a manager sees, so admin can jump in on any complaint
 * directly rather than only watching from the sidelines.
 */
export default function AdminComplaintsPage() {
  const [status, setStatus] = useState("");
  const [centerFilter, setCenterFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: centers } = useQuery({ queryKey: ["admin-centers-for-complaints"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const { data, isLoading } = useQuery({
    queryKey: ["admin-complaints", status],
    queryFn: () => complaintApi.all({ status: status || undefined, page: 1, page_size: 100 }),
  });

  const filtered = useMemo(() => {
    let items = data?.data || [];
    if (centerFilter) items = items.filter((c) => c.service_center_id === centerFilter);
    return items;
  }, [data, centerFilter]);

  const selected = filtered.find((c) => c.id === selectedId) || null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Complaints</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Platform-wide complaint oversight — click a complaint for the full thread.</p>
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="max-w-xs">
          <Select label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </Select>
        </div>
        <div className="max-w-xs">
          <Select label="Filter by service center" value={centerFilter} onChange={(e) => setCenterFilter(e.target.value)}>
            <option value="">All service centers</option>
            {(centers?.data || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={filtered}
        emptyTitle="No complaints"
        onRowClick={(c) => setSelectedId(c.id)}
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Customer", accessor: (c) => c.customer_name || "—" },
          { header: "Booking", accessor: (c) => <span className="font-mono-num">{c.booking_number || "—"}</span> },
          { header: "Service center", accessor: (c) => c.service_center_name || "—" },
          { header: "Priority", accessor: (c) => <Badge tone={PRIORITY_TONE[c.priority] || "neutral"}>{c.priority}</Badge> },
          { header: "Raised", accessor: (c) => format(c.created_at) },
          { header: "Status", accessor: (c) => <StatusBadge status={c.status} /> },
        ]}
      />

      <ComplaintDetailDrawer complaint={selected} onClose={() => setSelectedId(null)} />
    </div>
  );
}
