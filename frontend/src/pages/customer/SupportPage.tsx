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
  const { data, isLoading } = useQuery({ queryKey: ["my-complaints"], queryFn: () => complaintApi.mine({ page: 1, page_size: 50 }) });
  // The picker is built from the customer's own bookings — the ticket
  // inherits its service center (and therefore its manager) from the
  // booking, so there's no "pick the right branch" step to get wrong.
  const { data: bookings } = useQuery({ queryKey: ["my-bookings-for-complaint"], queryFn: () => bookingApi.myBookings({ page: 1, page_size: 100 }) });
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Complaint | null>(null);
  const [bookingId, setBookingId] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [replyText, setReplyText] = useState("");

  const replyMutation = useMutation({
    mutationFn: () => complaintApi.reply(selected!.id, { message: replyText.trim() }),
    onSuccess: () => {
      setReplyText("");
      queryClient.invalidateQueries({ queryKey: ["my-complaints"] });
    },
    onError: (err) => setError(getErrorMessage(err)),
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
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Support</p>
          <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">We're here to help</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Raise an issue about one of your bookings — it goes straight to the team that served you.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> New support request
        </Button>
      </div>

      {isLoading ? (
        <PageLoader />
      ) : !tickets.length ? (
        <EmptyState
          icon={LifeBuoy}
          title="No support requests"
          description="Something not right with a service? Raise it here and the branch that served you will pick it up."
          action={<Button onClick={() => setOpen(true)}>Raise an issue</Button>}
        />
      ) : (
        <div className="space-y-3">
          {tickets.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t)}
              className="flex w-full items-center gap-4 rounded-2xl border border-[#F3E5B5] bg-white p-4 text-left transition-all hover:border-[#E8A900]/50 hover:shadow-[0_10px_24px_rgba(60,40,0,0.07)] sm:p-5"
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
                  {t.replies?.length ? ` · ${t.replies.length} update${t.replies.length > 1 ? "s" : ""} from our team` : ""}
                </p>
              </div>
              <StatusBadge status={t.status} />
              <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />
            </button>
          ))}
        </div>
      )}

      {/* Ticket detail + the manager's comment thread */}
      <Modal open={!!selectedFresh} onClose={() => setSelected(null)} title={selectedFresh?.subject || "Support request"}>
        {selectedFresh && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={selectedFresh.status} />
              <span className="text-xs text-[var(--color-text-secondary)]">
                Booking <span className="font-mono-num font-semibold">{selectedFresh.booking_number || "—"}</span> · raised {format(selectedFresh.created_at)}
              </span>
            </div>

            <div className="rounded-xl bg-[#FAFAFA] p-3.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-black">Your message</p>
              <p className="mt-1 text-sm text-[var(--color-text-primary)]">{selectedFresh.description}</p>
            </div>

            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-black">Conversation</p>
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
                <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                  No updates yet — the branch manager has been notified and will respond here.
                </p>
              )}

              {/* The customer can ANSWER now — support used to be one-way. */}
              {!["resolved", "closed"].includes(selectedFresh.status) && (
                <div className="mt-3 flex gap-2">
                  <input
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    placeholder="Write a reply…"
                    className="flex-1 rounded-xl border border-[#F3E5B5] px-3.5 py-2.5 text-sm outline-none focus:border-[#E8A900]"
                  />
                  <Button
                    size="sm"
                    disabled={!replyText.trim()}
                    isLoading={replyMutation.isPending}
                    onClick={() => replyMutation.mutate()}
                  >
                    Send
                  </Button>
                </div>
              )}
            </div>

            {selectedFresh.resolution_note && (
              <div className="rounded-xl bg-green-50 p-3.5">
                <p className="text-xs font-semibold uppercase tracking-wide text-green-700">Resolution</p>
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
          <p className="rounded-xl bg-[#FAFAFA] p-3 text-xs text-gray-600">
            Every request is linked to a booking so it reaches the exact team that served you — pick the booking this is about.
          </p>
          <Select label="Which booking is this about?" value={bookingId} onChange={(e) => setBookingId(e.target.value)} required>
            <option value="">Select a booking…</option>
            {(bookings?.data || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.booking_number} — {format(b.scheduled_date)} · {b.combo_name || b.service_names?.join(", ") || "Service"}
              </option>
            ))}
          </Select>
          <Input label="What went wrong?" value={subject} onChange={(e) => setSubject(e.target.value)} required />
          <Input label="Tell us a bit more" value={description} onChange={(e) => setDescription(e.target.value)} required />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" disabled={!bookingId} isLoading={createMutation.isPending}>
            Submit request
          </Button>
        </form>
      </Modal>
    </div>
  );
}
