/**
 * Customer support — every ticket is pinned to one of the customer's OWN
 * bookings (no free-floating issues), which is what routes it to the
 * manager of the service center that served that booking. That manager's
 * comments and status changes land in the reply thread shown here — the
 * same thread the admin sees.
 */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, LifeBuoy, MessageSquare, Plus } from "lucide-react";
import { complaintApi } from "../../api/engagement";
import { bookingApi } from "../../api/booking";
import { Button, EmptyState, Input, Modal, PageLoader, Select, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatDateTime } from "../../lib/date";
import type { Complaint } from "../../types";

export default function SupportPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Complaint | null>(null);
  // While a ticket is open, check for the team's replies every 20 s.
  const { data, isLoading } = useQuery({
    queryKey: ["my-complaints"],
    queryFn: () => complaintApi.mine({ page: 1, page_size: 50 }),
    refetchInterval: selected ? 20000 : false,
  });
  // The picker is built from the customer's own bookings — the ticket
  // inherits its service center (and therefore its manager) from the
  // booking, so there's no "pick the right branch" step to get wrong.
  const { data: bookings } = useQuery({ queryKey: ["my-bookings-for-complaint"], queryFn: () => bookingApi.myBookings({ page: 1, page_size: 100 }) });
  const [open, setOpen] = useState(false);
  const [bookingId, setBookingId] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [replyText, setReplyText] = useState("");
  const [replyError, setReplyError] = useState("");

  const replyMutation = useMutation({
    mutationFn: () => complaintApi.reply(selected!.id, { message: replyText.trim() }),
    onSuccess: () => {
      setReplyText("");
      setReplyError("");
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
    },
    onError: (err) => setReplyError(getErrorMessage(err)),
  });

  // Arriving from a booking's "Need help?" button: open the form with
  // that booking already picked.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const fromBooking = searchParams.get("booking");
    if (fromBooking) {
      setBookingId(fromBooking);
      setOpen(true);
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Every request is treated as top priority by the team — asking the
  // customer to rate their own issue adds nothing, so we don't.
  const createMutation = useMutation({
    mutationFn: () => complaintApi.create({ booking_id: bookingId, subject, description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
      setOpen(false);
      setBookingId("");
      setSubject("");
      setDescription("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const tickets = data?.data || [];
  // Keep the drawer in sync after a manager replies while it's open.
  const selectedFresh = selected ? tickets.find((t) => t.id === selected.id) || selected : null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="font-display text-2xl font-bold text-black">Support</h1>
        <Button
          variant="info"
          onClick={() => {
            setError("");
            setOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> New request
        </Button>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !tickets.length ? (
        <EmptyState
          icon={LifeBuoy}
          title="No support requests"
          description="Something not right with a service? Raise it here."
          action={
            <Button variant="info" onClick={() => setOpen(true)}>
              Raise an issue
            </Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {tickets.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t)}
              className="flex w-full items-center gap-4 rounded-2xl border border-[#F3E5B5] bg-white p-4 text-left transition-all hover:shadow-[0_8px_24px_rgba(17,24,39,0.08)] sm:p-5"
            >
              <span className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black sm:flex">
                <MessageSquare className="h-5 w-5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-semibold text-black">{t.subject}</p>
                </div>
                <p className="mt-0.5 truncate text-xs text-gray-400">
                  <span className="font-mono-num">{t.booking_number || "—"}</span> · raised {format(t.created_at)}
                  {t.replies?.length ? ` · ${t.replies.length} repl${t.replies.length > 1 ? "ies" : "y"}` : ""}
                </p>
              </div>
              <StatusBadge status={t.status} />
              <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
            </button>
          ))}
        </div>
      )}

      {/* Ticket detail + the manager's comment thread */}
      <Modal
        open={!!selectedFresh}
        onClose={() => {
          setSelected(null);
          setReplyError("");
        }}
        title={selectedFresh?.subject || "Support request"}
      >
        {selectedFresh && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={selectedFresh.status} />
              <span className="text-xs text-[var(--color-text-secondary)]">
                Booking <span className="font-mono-num font-semibold">{selectedFresh.booking_number || "—"}</span> · raised {format(selectedFresh.created_at)}
              </span>
            </div>

            <div className="rounded-xl bg-[#FAFAFA] p-3.5">
              <p className="text-xs text-gray-500">Your message</p>
              <p className="mt-1 text-sm text-[var(--color-text-primary)]">{selectedFresh.description}</p>
            </div>

            <div>
              <p className="text-xs text-gray-500">Conversation</p>
              {selectedFresh.replies?.length ? (
                <div className="mt-2 space-y-2.5">
                  {selectedFresh.replies.map((r, i) => (
                    <div key={i} className={`rounded-xl border p-3 ${r.author_role === "customer" ? "border-[#F3E5B5] bg-[#FAFAFA]" : "border-[#F3E5B5] bg-white"}`}>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs font-bold capitalize text-black">{r.author_role === "customer" ? "You" : `Blussit ${r.author_role}`}</span>
                        <span className="text-[11px] text-gray-400">{formatDateTime(r.created_at)}</span>
                      </div>
                      <p className="mt-1 text-sm text-[var(--color-text-primary)]">{r.message}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-[var(--color-text-secondary)]">No updates yet.</p>
              )}

              {/* The customer can ANSWER now — support used to be one-way. */}
              {!["resolved", "closed"].includes(selectedFresh.status) && (
                <div className="mt-3 flex gap-2">
                  <input
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    placeholder="Write a reply…"
                    className="flex-1 rounded-xl border border-[#F3E5B5] px-3.5 py-2.5 text-sm outline-none focus:border-black"
                  />
                  <Button
                    variant="info"
                    size="sm"
                    disabled={!replyText.trim()}
                    isLoading={replyMutation.isPending}
                    onClick={() => replyMutation.mutate()}
                  >
                    Send
                  </Button>
                </div>
              )}
              {replyError && <p className="mt-2 text-sm text-[var(--color-error)]">{replyError}</p>}
            </div>

            {selectedFresh.resolution_note && (
              <div className="rounded-xl bg-green-50 p-3.5">
                <p className="text-xs text-green-700">Resolution</p>
                <p className="mt-1 text-sm text-green-900">{selectedFresh.resolution_note}</p>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* New request */}
      <Modal open={open} onClose={() => setOpen(false)} title="Raise a support request">
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
            {/* Arrived from an older booking's "Need help?" that isn't in the recent list. */}
            {bookingId && !(bookings?.data || []).some((b) => b.id === bookingId) && <option value={bookingId}>The booking you came from</option>}
            {(bookings?.data || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.booking_number} — {format(b.scheduled_date)} · {b.combo_name || b.service_names?.join(", ") || "Service"}
              </option>
            ))}
          </Select>
          <Input
            label="What went wrong?"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            hint={subject.trim().length > 0 && subject.trim().length < 3 ? "At least 3 characters." : undefined}
            required
          />
          <Input
            label="Tell us a bit more"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            hint={description.trim().length > 0 && description.trim().length < 5 ? "At least 5 characters." : undefined}
            required
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button
            type="submit"
            variant="info"
            className="w-full"
            disabled={!bookingId || subject.trim().length < 3 || description.trim().length < 5}
            isLoading={createMutation.isPending}
          >
            Submit request
          </Button>
        </form>
      </Modal>
    </div>
  );
}
