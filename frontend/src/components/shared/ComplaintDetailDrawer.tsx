import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { complaintApi } from "../../api/engagement";
import { Badge, Button, Modal, Select, StatusBadge } from "../../components/ui";
import { BookingDetailDrawer } from "./BookingDetailDrawer";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { Complaint } from "../../types";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = {
  urgent: "error",
  high: "error",
  medium: "warning",
  low: "neutral",
};

/**
 * Manager/admin complaint detail — the popup a clicked complaint row opens.
 * Shows the full description, a direct link into the booking it's about
 * (every complaint is now tied to exactly one, never a free-floating
 * issue), and an append-only reply thread so a manager's "here's what I
 * found / here's what I did / this is resolved" stays visible as a running
 * history rather than a single note that gets overwritten every update.
 */
export function ComplaintDetailDrawer({ complaint, onClose }: { complaint: Complaint | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [bookingOpen, setBookingOpen] = useState(false);

  const { data: booking } = useQuery({
    queryKey: ["complaint-booking", complaint?.booking_id],
    queryFn: () => bookingApi.get(complaint!.booking_id!),
    enabled: !!complaint?.booking_id && bookingOpen,
  });

  const replyMutation = useMutation({
    mutationFn: () => complaintApi.reply(complaint!.id, { message, status: status || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["center-complaints-page"] });
      queryClient.invalidateQueries({ queryKey: ["admin-complaints"] });
      setMessage("");
      setStatus("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (!complaint) return null;

  return (
    <>
      <Modal open={!!complaint} onClose={onClose} title={complaint.subject} maxWidth="max-w-xl">
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={complaint.status} />
            <Badge tone={PRIORITY_TONE[complaint.priority] || "neutral"}>{complaint.priority}</Badge>
            <span className="text-xs text-[var(--color-text-secondary)]">Raised {format(complaint.created_at)}</span>
          </div>

          <div className="rounded-xl bg-gray-50 p-3.5">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Customer</p>
            <p className="mt-0.5 text-sm text-[var(--color-text-primary)]">{complaint.customer_name || "—"}</p>
          </div>

          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Description</p>
            <p className="mt-1 text-sm text-[var(--color-text-primary)]">{complaint.description}</p>
          </div>

          {complaint.booking_id ? (
            <button
              type="button"
              onClick={() => setBookingOpen(true)}
              className="flex w-full items-center justify-between rounded-xl border border-gray-200 px-3.5 py-2.5 text-sm hover:border-[var(--color-primary)]"
            >
              <span className="text-[var(--color-text-secondary)]">
                About booking <span className="font-mono-num font-medium text-[var(--color-text-primary)]">{complaint.booking_number || complaint.booking_id}</span>
              </span>
              <span className="text-xs font-medium text-[var(--color-primary)]">View booking →</span>
            </button>
          ) : (
            <p className="text-xs text-[var(--color-text-secondary)]">This complaint predates booking-linking and isn't tied to a specific booking.</p>
          )}

          <div>
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">
              <MessageSquare className="h-3.5 w-3.5" /> Updates &amp; replies
            </p>
            {!complaint.replies?.length ? (
              <p className="rounded-xl bg-gray-50 p-3 text-sm text-[var(--color-text-secondary)]">No updates yet.</p>
            ) : (
              <div className="space-y-2.5">
                {complaint.replies.map((r, i) => (
                  <div key={i} className="rounded-xl border border-gray-100 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold capitalize text-[var(--color-text-primary)]">{r.author_role}</span>
                      <span className="text-xs text-[var(--color-text-secondary)]">{format(r.created_at)}</span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--color-text-primary)]">{r.message}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-3 border-t border-gray-100 pt-4">
            <p className="text-sm font-semibold text-[var(--color-text-primary)]">Add an update</p>
            <textarea
              className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
              rows={3}
              placeholder="What did you find, what are you doing, what got resolved…"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
            />
            <Select label="Update status (optional)" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Keep current status</option>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </Select>
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={!message.trim()} isLoading={replyMutation.isPending} onClick={() => replyMutation.mutate()}>
              Post update
            </Button>
          </div>
        </div>
      </Modal>

      {bookingOpen && booking && <BookingDetailDrawer booking={booking} onClose={() => setBookingOpen(false)} />}
    </>
  );
}
