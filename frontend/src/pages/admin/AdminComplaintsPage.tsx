import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { Button, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

export default function AdminComplaintsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["admin-complaints", status],
    queryFn: () => complaintApi.all({ status: status || undefined, page: 1, page_size: 30 }),
  });
  const [selected, setSelected] = useState<Complaint | null>(null);
  const [newStatus, setNewStatus] = useState("in_progress");
  const [note, setNote] = useState("");

  const updateMutation = useMutation({
    mutationFn: () => complaintApi.update(selected!.id, { status: newStatus, resolution_note: note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-complaints"] });
      setSelected(null);
      setNote("");
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Complaints</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Platform-wide complaint oversight.</p>
      </div>

      <div className="max-w-xs">
        <Select label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In progress</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </Select>
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No complaints"
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Priority", accessor: (c) => <span className="capitalize">{c.priority}</span> },
          { header: "Raised", accessor: (c) => format(c.created_at) },
          { header: "Status", accessor: (c) => <StatusBadge status={c.status} /> },
          {
            header: "",
            accessor: (c) => (
              <Button size="sm" variant="outline" onClick={() => { setSelected(c); setNewStatus(c.status); }}>
                Update
              </Button>
            ),
          },
        ]}
      />

      <Modal open={!!selected} onClose={() => setSelected(null)} title="Update complaint">
        {selected && (
          <div className="space-y-4">
            <p className="text-sm text-[var(--color-text-secondary)]">{selected.description}</p>
            <Select label="Status" value={newStatus} onChange={(e) => setNewStatus(e.target.value)}>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </Select>
            <Input label="Resolution note" value={note} onChange={(e) => setNote(e.target.value)} />
            <Button className="w-full" isLoading={updateMutation.isPending} onClick={() => updateMutation.mutate()}>
              Save
            </Button>
          </div>
        )}
      </Modal>
    </div>
  );
}
