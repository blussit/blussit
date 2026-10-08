import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { complaintApi } from "../../api/engagement";
import { Badge, Button, ErrorState, Modal, Select, StatusBadge } from "../../components/ui";
import { BookingDetailDrawer } from "./BookingDetailDrawer";
import { CustomerDetailDrawer } from "./CustomerDetailDrawer";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatDateTime } from "../../lib/date";
import type { Complaint } from "../../types";
import { toTitle } from "../../lib/titleCase";

const PRIORITY_TONE: Record<string, "error" | "warning" | "neutral"> = {
  urgent: "error",
  high: "error",
  medium: "warning",
  low: "neutral",
};

const MAX_MESSAGE = 2000;

/**
 * Manager/admin ticket detail — the popup a clicked complaint row opens.
 * Shows the full description, a direct link into the booking it's about,
 * and an append-only update thread ("what I found / what I did / resolved")
 * that every party sees.
 *
 * The ticket shown here is read from the server (GET /complaints/{id}) and
 * cached under ["complaint", id]; every post writes the server's answer
 * straight into that cache. It used to render a COPY of the clicked list
 * row that two writers kept overwriting — the post's own result and the
 * list refetch it triggered — so whichever landed last won, and a later
 * update could vanish behind an older copy ("the first update posts, the
 * next one doesn't"). The page's row is now only the instant first paint.
 */
export function ComplaintDetailDrawer({
  complaint,
  onClose,
  onUpdated,
}: {
  complaint: Complaint | null;
  onClose: () => void;
  /** The complaint as saved — lets the page keep showing it even when the
   *  change moves it out of the list's current filter/page. */
  onUpdated?: (complaint: Complaint) => void;
}) {
  const queryClient = useQueryClient();
  const id = complaint?.id;
  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [bookingOpen, setBookingOpen] = useState(false);
  const [customerOpen, setCustomerOpen] = useState(false);
  const threadEndRef = useRef<HTMLDivElement>(null);

  // A different ticket starts with an empty form — a half-typed update or
  // an old error must never carry over from the previous ticket.
  useEffect(() => {
    setMessage("");
    setStatus("");
    setError("");
    setBookingOpen(false);
    setCustomerOpen(false);
  }, [id]);

  const freshQuery = useQuery({
    queryKey: ["complaint", id],
    queryFn: () => complaintApi.get(id!),
    enabled: !!id,
    // Customer replies land while the drawer is open.
    refetchInterval: 20000,
  });
  const fresh = freshQuery.data;
  const ticket: Complaint | null = fresh && fresh.id === id ? fresh : complaint;
  // The list row may not carry the thread — if the full ticket didn't load,
  // "No updates yet" would be a guess, so say the read failed instead.
  const threadFailed = freshQuery.isError && !fresh;

  const refreshLists = () => {
    queryClient.invalidateQueries({ queryKey: ["center-complaints-page"] });
    queryClient.invalidateQueries({ queryKey: ["admin-complaints"] });
    queryClient.invalidateQueries({ queryKey: ["kpi-drill-complaints"] });
    queryClient.invalidateQueries({ queryKey: ["manager-dashboard"] });
    // A society issue's status moves the society page's open count.
    queryClient.invalidateQueries({ queryKey: ["society-issues"] });
    queryClient.invalidateQueries({ queryKey: ["society"] });
  };

  const applySaved = (saved: Complaint) => {
    queryClient.setQueryData(["complaint", saved.id], saved);
    onUpdated?.(saved);
    refreshLists();
  };

  const bookingQuery = useQuery({
    queryKey: ["complaint-booking", ticket?.booking_id],
    queryFn: () => bookingApi.get(ticket!.booking_id!),
    enabled: !!ticket?.booking_id && bookingOpen,
  });
  const booking = bookingQuery.data;

  const replyMutation = useMutation({
    // Everything the post needs travels as variables — nothing is read
    // from a render-time closure that a later render could have replaced.
    mutationFn: (vars: { id: string; message: string; status?: string }) =>
      complaintApi.reply(vars.id, { message: vars.message, status: vars.status }),
    onSuccess: (saved, vars) => {
      if (saved) applySaved(saved);
      // Only clear the box if it still holds what was just posted.
      setMessage((cur) => (cur.trim() === vars.message ? "" : cur));
      setStatus("");
      setError("");
      requestAnimationFrame(() => threadEndRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const priorityMutation = useMutation({
    mutationFn: (vars: { id: string; priority: string }) => complaintApi.update(vars.id, { priority: vars.priority }),
    onSuccess: (saved) => {
      if (saved) applySaved(saved);
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (!ticket) return null;

  const trimmed = message.trim();
  // A status change alone is enough — the server writes "Status changed to
  // …" on the thread, so closing a ticket needs no typed note.
  const statusChanges = !!status && status !== ticket.status;
  const canPost = (!!trimmed || statusChanges) && trimmed.length <= MAX_MESSAGE && !replyMutation.isPending;
  const post = () => {
    if (!canPost) return;
    setError("");
    replyMutation.mutate({ id: ticket.id, message: trimmed, status: status || undefined });
  };

  return (
    <>
      <Modal open={!!complaint} onClose={onClose} title={ticket.subject} maxWidth="max-w-xl">
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={ticket.status} />
            <Badge tone={PRIORITY_TONE[ticket.priority] || "neutral"}>{toTitle(ticket.priority)}</Badge>
            <span className="text-xs text-[var(--color-text-secondary)]">Raised {format(ticket.created_at)}</span>
            <Select
              compact
              wrapperClassName="w-full sm:ml-auto sm:w-44"
              aria-label="Change priority"
              value={ticket.priority}
              disabled={priorityMutation.isPending}
              onChange={(e) => priorityMutation.mutate({ id: ticket.id, priority: e.target.value })}
            >
              <option value="low">Low Priority</option>
              <option value="medium">Medium Priority</option>
              <option value="high">High Priority</option>
              <option value="urgent">Urgent</option>
            </Select>
          </div>

          <div className="flex items-center justify-between gap-3 rounded-xl bg-[var(--ui-icon-bg,#f9fafb)] p-3.5">
            <div className="min-w-0">
              <p className="text-xs text-[var(--color-text-secondary)]">Customer</p>
              <p className="mt-0.5 truncate text-sm font-medium text-[var(--color-text-primary)]">{ticket.customer_name || "—"}</p>
            </div>
            {ticket.customer_id && (
              <button
                type="button"
                onClick={() => setCustomerOpen(true)}
                className="shrink-0 text-xs font-semibold text-[var(--color-primary)] hover:underline"
              >
                Bookings &amp; Plans
              </button>
            )}
          </div>

          <div>
            <p className="text-xs text-[var(--color-text-secondary)]">Description</p>
            <p className="mt-1 whitespace-pre-line break-words text-sm text-[var(--color-text-primary)]">{ticket.description}</p>
          </div>

          {ticket.category === "society" ? (
            <div className="rounded-xl border border-[#E4E9F1] bg-[#EEF3FA] px-3.5 py-2.5 text-sm" data-testid="complaint-society-tag">
              <p className="font-semibold text-[#0E1A33]">Society Issue · {ticket.issue_label || "Issue"}</p>
              <p className="mt-0.5 text-xs text-[#5F6878]">
                {[ticket.society_name, ticket.flat, ticket.registration_number].filter(Boolean).join(" · ")}
              </p>
            </div>
          ) : ticket.booking_id ? (
            <button
              type="button"
              onClick={() => setBookingOpen(true)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-[var(--color-card-border)] px-3.5 py-2.5 text-sm hover:border-[var(--color-primary)]"
            >
              <span className="min-w-0 truncate text-[var(--color-text-secondary)]">
                About booking{" "}
                <span className="font-mono-num font-medium text-[var(--color-text-primary)]">{ticket.booking_number || ticket.booking_id}</span>
              </span>
              <span className="shrink-0 text-xs font-semibold text-[var(--color-primary)]">View Booking →</span>
            </button>
          ) : (
            <p className="text-xs text-[var(--color-text-secondary)]">This complaint predates booking-linking and isn't tied to a specific booking.</p>
          )}
          {bookingOpen && bookingQuery.isError && !booking && (
            <p role="alert" className="text-xs text-[var(--color-text-secondary)]">
              Couldn't load this booking.{" "}
              <button
                type="button"
                className="font-semibold text-[var(--color-primary)] hover:underline disabled:opacity-60"
                disabled={bookingQuery.isFetching}
                onClick={() => void bookingQuery.refetch()}
              >
                Try Again
              </button>
            </p>
          )}

          <div>
            <p className="mb-2 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
              <MessageSquare className="h-3.5 w-3.5" /> Updates &amp; replies
              {!!ticket.replies?.length && <span className="font-mono-num">({ticket.replies.length})</span>}
            </p>
            {threadFailed && !ticket.replies?.length ? (
              <ErrorState message="Couldn't load the replies." onRetry={() => void freshQuery.refetch()} busy={freshQuery.isFetching} className="p-4" />
            ) : !ticket.replies?.length ? (
              <p className="rounded-xl bg-[var(--ui-icon-bg,#f9fafb)] p-3 text-sm text-[var(--color-text-secondary)]">No updates yet.</p>
            ) : (
              <div className="space-y-2.5">
                {ticket.replies.map((r, i) => {
                  const fromCustomer = r.author_role === "customer";
                  return (
                    <div
                      key={`${r.created_at}-${i}`}
                      className={`rounded-xl border p-3 ${fromCustomer ? "border-[var(--color-card-border)] bg-white" : "border-transparent bg-[var(--color-primary-light)]"}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5">
                        <span className="text-xs font-semibold capitalize text-[var(--color-text-primary)]">
                          {(() => {
                            const who = r.author_name || (fromCustomer ? ticket.customer_name : null);
                            return who ? (
                              <>
                                <span className="normal-case">{who}</span>
                                <span className="ml-1.5 font-normal text-[var(--color-text-secondary)]">{r.author_role}</span>
                              </>
                            ) : (
                              r.author_role
                            );
                          })()}
                        </span>
                        <span className="text-xs text-[var(--color-text-secondary)]">{formatDateTime(r.created_at)}</span>
                      </div>
                      <p className="mt-1 whitespace-pre-line break-words text-sm text-[var(--color-text-primary)]">{r.message}</p>
                    </div>
                  );
                })}
                <div ref={threadEndRef} />
              </div>
            )}
          </div>

          <div className="space-y-3 border-t border-[var(--color-card-border)] pt-4">
            <label htmlFor="complaint-update" className="block text-sm font-semibold text-[var(--color-text-primary)]">
              Add An Update
            </label>
            <textarea
              id="complaint-update"
              className="w-full rounded-xl border px-3.5 py-2.5 text-sm"
              rows={3}
              maxLength={MAX_MESSAGE}
              placeholder="What did you find, what are you doing, what got resolved…"
              value={message}
              onChange={(e) => {
                setMessage(e.target.value);
                if (error) setError("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  post();
                }
              }}
            />
            <div className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
              <span>The customer sees every update.</span>
              <span className="font-mono-num">
                {message.length}/{MAX_MESSAGE}
              </span>
            </div>
            <Select label="Update Status (Optional)" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Keep Current Status</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </Select>
            {statusChanges && !trimmed && (
              <p className="text-xs text-[var(--color-text-secondary)]">No note needed — the thread will say the status changed.</p>
            )}
            {error && (
              <p role="alert" className="text-sm text-[var(--color-error)]">
                {error}
              </p>
            )}
            <Button className="w-full" disabled={!canPost} isLoading={replyMutation.isPending} onClick={post}>
              {!trimmed && statusChanges ? "Update Status" : "Post Update"}
            </Button>
          </div>
        </div>
      </Modal>

      {bookingOpen && booking && <BookingDetailDrawer booking={booking} hasComplaint onClose={() => setBookingOpen(false)} />}
      <CustomerDetailDrawer customerId={customerOpen ? ticket.customer_id : null} onClose={() => setCustomerOpen(false)} />
    </>
  );
}
