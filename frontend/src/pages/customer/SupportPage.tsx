import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { complaintApi } from "../../api/engagement";
import { Button, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

export default function SupportPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["my-complaints"], queryFn: () => complaintApi.mine({ page: 1, page_size: 20 }) });
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [error, setError] = useState("");

  const createMutation = useMutation({
    mutationFn: () => complaintApi.create({ subject, description, priority }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
      setOpen(false);
      setSubject("");
      setDescription("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Support</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Raise an issue and our team will get back to you.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> New complaint
        </Button>
      </div>

      <DataTable<Complaint>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No complaints raised"
        columns={[
          { header: "Subject", accessor: (c) => c.subject },
          { header: "Priority", accessor: (c) => <span className="capitalize">{c.priority}</span> },
          { header: "Raised on", accessor: (c) => format(c.created_at) },
          { header: "Status", accessor: (c) => <StatusBadge status={c.status} /> },
        ]}
      />

      <Modal open={open} onClose={() => setOpen(false)} title="Raise a complaint">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createMutation.mutate();
          }}
        >
          <Input label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} required />
          <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} required />
          <Select label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </Select>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending}>
            Submit
          </Button>
        </form>
      </Modal>
    </div>
  );
}
