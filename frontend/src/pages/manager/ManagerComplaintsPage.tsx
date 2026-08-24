import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { complaintApi } from "../../api/engagement";
import { Button, DataTable, Modal, Select, StatusBadge, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

export default function ManagerComplaintsPage() {
  const { user } = useAuth();
  const centerId = user?.service_center_id || "";
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Complaint | null>(null);
  const [status, setStatus] = useState("in_progress");
  const [note, setNote] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["center-complaints-page", centerId],
    queryFn: () => complaintApi.forCenter(centerId, { page: 1, page_size: 30 }),
    enabled: !!centerId,
  });

  const updateMutation = useMutation({
    mutationFn: () => complaintApi.update(selected!.id, { status, resolution_note: note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["center-complaints-page"] });
      setSelected(null);
      setNote("");
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Complaints</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Resolve customer issues for your service center.</p>
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
              <Button size="sm" variant="outline" onClick={() => { setSelected(c); setStatus(c.status === "open" ? "in_progress" : c.status); }}>
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
            <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
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
