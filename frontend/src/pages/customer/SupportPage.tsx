import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { complaintApi } from "../../api/engagement";
import { bookingApi } from "../../api/booking";
import { Button, DataTable, Input, Modal, Select, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

export default function SupportPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["my-complaints"], queryFn: () => complaintApi.mine({ page: 1, page_size: 20 }) });
  // Every complaint has to be about a specific booking — no free-floating
  // "raise an issue about nothing in particular". Offering a picker built
  // from the customer's own bookings (rather than a free-text field) is
  // also what makes the routing to the right manager reliable: the
  // complaint inherits its service center straight from the chosen booking.
  const { data: bookings } = useQuery({ queryKey: ["my-bookings-for-complaint"], queryFn: () => bookingApi.myBookings({ page: 1, page_size: 100 }) });
  const [open, setOpen] = useState(false);
  const [bookingId, setBookingId] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("medium");
  const [error, setError] = useState("");

  const createMutation = useMutation({
    mutationFn: () => complaintApi.create({ booking_id: bookingId, subject, description, priority }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
      setOpen(false);
      setBookingId("");
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
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Raise an issue about one of your bookings and our team will get back to you.</p>
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
          { header: "Booking", accessor: (c) => <span className="font-mono-num">{c.booking_number || "—"}</span> },
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
          <Select label="Which booking is this about?" value={bookingId} onChange={(e) => setBookingId(e.target.value)} required>
            <option value="">Select a booking…</option>
            {(bookings?.data || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.booking_number} — {format(b.scheduled_date)} · {b.combo_name || b.service_names?.join(", ") || "Service"}
              </option>
            ))}
          </Select>
          <Input label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} required />
          <Input label="Description" value={description} onChange={(e) => setDescription(e.target.value)} required />
          <Select label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="urgent">Urgent</option>
          </Select>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" disabled={!bookingId} isLoading={createMutation.isPending}>
            Submit
          </Button>
        </form>
      </Modal>
    </div>
  );
}
