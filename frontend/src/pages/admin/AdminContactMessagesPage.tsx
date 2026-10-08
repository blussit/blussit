import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { adminContactMessageApi } from "../../api/admin";
import { Button, DataTable, Modal } from "../../components/ui";
import { formatDateTime } from "../../lib/date";
import type { ContactMessage } from "../../types";

export default function AdminContactMessagesPage() {
  const [page, setPage] = useState(1);
  // A long message was clamped to two lines with no way to read the rest —
  // the row now opens the whole thing.
  const [open, setOpen] = useState<ContactMessage | null>(null);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["admin-contact-messages", page],
    queryFn: () => adminContactMessageApi.list({ page, page_size: 20 }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Contact Messages</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Submissions from the landing page's "Contact Us" form.</p>
      </div>

      <DataTable<ContactMessage>
        isLoading={isLoading}
        data={data?.data || []}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle="No Messages Yet"
        emptyDescription="Contact form submissions from the landing page will show up here."
        onRowClick={setOpen}
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
              <a
                href={`mailto:${m.email}`}
                onClick={(e) => e.stopPropagation()}
                className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-primary)] hover:underline"
              >
                <Mail className="h-3.5 w-3.5" /> Reply
              </a>
            ),
          },
        ]}
      />
      <Modal open={!!open} onClose={() => setOpen(null)} title={open ? `Message From ${open.name}` : "Message"}>
        {open && (
          <div className="space-y-3 text-sm">
            <p className="text-xs text-gray-500">
              {formatDateTime(open.created_at)} · {open.phone}
              {open.email ? ` · ${open.email}` : ""}
            </p>
            <p className="whitespace-pre-wrap break-words leading-relaxed text-[var(--color-text-primary)]">{open.message}</p>
            <div className="flex flex-wrap gap-2 pt-1">
              {open.email && (
                <a href={`mailto:${open.email}`} className="inline-flex items-center gap-1.5 rounded-[10px] bg-[var(--color-primary)] px-3.5 py-2 text-sm font-semibold text-white">
                  <Mail className="h-4 w-4" /> Reply By Email
                </a>
              )}
              {open.phone && (
                <a href={`tel:${open.phone}`} className="inline-flex items-center gap-1.5 rounded-[10px] border border-[#E4E9F1] px-3.5 py-2 text-sm font-semibold text-[var(--color-text-primary)]">
                  Call {open.phone}
                </a>
              )}
            </div>
          </div>
        )}
      </Modal>

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
