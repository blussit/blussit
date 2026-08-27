import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { Badge, DataTable, StatusBadge } from "../../components/ui";
import { ComplaintDetailDrawer } from "../../components/shared/ComplaintDetailDrawer";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = { urgent: "error", high: "error", medium: "warning", low: "neutral" };

/**
 * Every complaint here is already scoped to this manager's own service
 * center by the backend (complaints inherit service_center_id from the
 * booking they're about, and list_for_center enforces ensure_own_center) —
 * so what a manager sees is exactly "complaints about my center's
 * bookings", never anyone else's. Clicking a row opens the full detail +
 * reply thread (ComplaintDetailDrawer) instead of a bare status editor.
 */
export default function ManagerComplaintsPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["center-complaints-page", centerId],
    queryFn: () => complaintApi.forCenter(centerId, { page: 1, page_size: 50 }),
    enabled: !!centerId,
  });

  const selected = data?.data.find((c) => c.id === selectedId) || null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Complaints</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Resolve customer issues for your service center — click a complaint for the full thread.</p>
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No complaints"
        onRowClick={(c) => setSelectedId(c.id)}
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Customer", accessor: (c) => c.customer_name || "—" },
          { header: "Booking", accessor: (c) => <span className="font-mono-num">{c.booking_number || "—"}</span> },
          { header: "Priority", accessor: (c) => <Badge tone={PRIORITY_TONE[c.priority] || "neutral"}>{c.priority}</Badge> },
          { header: "Raised", accessor: (c) => format(c.created_at) },
          { header: "Status", accessor: (c) => <StatusBadge status={c.status} /> },
        ]}
      />

      <ComplaintDetailDrawer complaint={selected} onClose={() => setSelectedId(null)} />
    </div>
  );
}
