import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { auditLogApi } from "../../api/admin";
import { Button, DataTable } from "../../components/ui";
import { formatDateTime } from "../../lib/date";

interface AuditRow {
  id: string;
  actor_role: string;
  action: string;
  module: string;
  target_id?: string;
  created_at: string;
}

export default function AdminAuditLogsPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ["admin-audit-logs", page],
    queryFn: () => auditLogApi.list({ page, page_size: 20 }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Audit logs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">A record of every sensitive admin/manager action.</p>
      </div>

      <DataTable<AuditRow>
        isLoading={isLoading}
        data={(data?.data || []) as unknown as AuditRow[]}
        emptyTitle="No audit logs yet"
        columns={[
          { header: "Action", accessor: (a) => <span className="font-mono-num text-xs">{a.action}</span> },
          { header: "Module", accessor: (a) => a.module },
          { header: "Role", accessor: (a) => <span className="capitalize">{a.actor_role}</span> },
          { header: "When", accessor: (a) => formatDateTime(a.created_at) },
        ]}
      />

      {data && data.meta.total_pages > 1 && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <Button size="sm" variant="outline" disabled={page >= data.meta.total_pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      )}
    </div>
  );
}
