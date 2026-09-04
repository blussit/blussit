import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { adminContactMessageApi } from "../../api/admin";
import { Button, DataTable } from "../../components/ui";
import { formatDateTime } from "../../lib/date";
import type { ContactMessage } from "../../types";

export default function AdminContactMessagesPage() {
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-contact-messages", page],
    queryFn: () => adminContactMessageApi.list({ page, page_size: 20 }),
  });
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ContactMessage["status"] }) => adminContactMessageApi.updateStatus(id, status),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-contact-messages"] }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Contact messages</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Submissions from the landing page's "Contact Us" form.</p>
      </div>

      <DataTable<ContactMessage>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No messages yet"
        emptyDescription="Contact form submissions from the landing page will show up here."
        columns={[
          { header: "Name", accessor: (m) => m.name },
          {
            header: "Contact",
            accessor: (m) => (
              <div className="text-xs">
                <p>{m.phone}</p>
                <p className="text-[var(--color-text-secondary)]">{m.email}</p>
              </div>
            ),
          },
          { header: "Vehicle", accessor: (m) => <span className="text-xs">{m.vehicle_type || "—"}</span> },
          { header: "Topic", accessor: (m) => <span className="text-xs">{m.topic || "General Enquiry"}</span> },
          { header: "Message", accessor: (m) => <span className="line-clamp-2 max-w-md text-xs">{m.message}</span> },
          {
            header: "Status",
            accessor: (m) => (
              <select
                aria-label={`Status for ${m.name}`}
                value={m.status || "NEW"}
                disabled={statusMutation.isPending}
                onChange={(event) => statusMutation.mutate({ id: m.id, status: event.target.value as ContactMessage["status"] })}
                className="rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
              >
                <option value="NEW">NEW</option>
                <option value="CONTACTED">CONTACTED</option>
                <option value="IN_PROGRESS">IN PROGRESS</option>
                <option value="RESOLVED">RESOLVED</option>
              </select>
            ),
          },
          { header: "Received", accessor: (m) => formatDateTime(m.created_at) },
          {
            header: "",
            accessor: (m) => m.email ? (
              <a href={`mailto:${m.email}`} className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-primary)] hover:underline">
                <Mail className="h-3.5 w-3.5" /> Reply
              </a>
            ) : null,
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
