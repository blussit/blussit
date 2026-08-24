import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { adminContactMessageApi } from "../../api/admin";
import { Button, DataTable } from "../../components/ui";
import { formatDateTime } from "../../lib/date";
import type { ContactMessage } from "../../types";

export default function AdminContactMessagesPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useQuery({
    queryKey: ["admin-contact-messages", page],
    queryFn: () => adminContactMessageApi.list({ page, page_size: 20 }),
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
          { header: "Message", accessor: (m) => <span className="line-clamp-2 max-w-md text-xs">{m.message}</span> },
          { header: "Received", accessor: (m) => formatDateTime(m.created_at) },
          {
            header: "",
            accessor: (m) => (
              <a href={`mailto:${m.email}`} className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-primary)] hover:underline">
                <Mail className="h-3.5 w-3.5" /> Reply
              </a>
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
