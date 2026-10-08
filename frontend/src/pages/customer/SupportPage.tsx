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
import { Building2, ChevronRight, LifeBuoy, MessageSquare, Plus } from "lucide-react";
import { complaintApi, subscriptionApi } from "../../api/engagement";
import { vehicleApi } from "../../api/profile";
import { SocietyIssueModal, type IssueSociety } from "../../components/society/SocietyIssueModal";
import { bookingApi } from "../../api/booking";
import { Button, EmptyState, ErrorState, Input, Modal, PageLoader, Select, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format, formatDateTime } from "../../lib/date";
import type { Complaint } from "../../types";
import { PageHeader, WhatsAppGlyph } from "../../components/customer/ui";
import { bookingServiceTitle, bookingTypeName } from "../../components/customer/cars";
import { openBlussitWhatsApp } from "../../components/public/WhatsAppFloatingButton";

export default function SupportPage() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Complaint | null>(null);
  // While a ticket is open, check for the team's replies every 20 s.
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
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
  // Society residents report society-service issues (missed daily wash,
  // captain no-show…) without a booking — routed to the society's manager.
  const [societyIssueOpen, setSocietyIssueOpen] = useState(false);
  const { data: subs } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const societySubs = (subs || []).filter((s) => s.society_id && s.status === "active");
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, enabled: societySubs.length > 0 });
  const issueSocieties: IssueSociety[] = Array.from(new Map(societySubs.map((s) => [s.society_id!, s])).values()).map((first) => ({
    id: first.society_id!,
    name: first.society_name || "Your society",
    cars: societySubs
      .filter((s) => s.society_id === first.society_id && s.vehicle_id)
      .map((s) => ({ vehicle_id: s.vehicle_id!, registration_number: vehicles?.find((v) => v.id === s.vehicle_id)?.registration_number || "Car" })),
  }));

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
      <PageHeader
        back="/app/profile"
        title="Help & Support"
        right={
          <Button
            variant="info"
            onClick={() => {
              setError("");
              setOpen(true);
            }}
          >
            <Plus className="h-4 w-4" />
            <span className="sm:hidden">New</span>
            <span className="hidden sm:inline">New Request</span>
          </Button>
        }
      />

      {/* Quickest route for anything urgent on the day. */}
      <button
        type="button"
        onClick={() => openBlussitWhatsApp("Hi Blussit, I need help with my account.")}
        className="flex w-full items-center gap-3.5 rounded-2xl border border-[#E4E9F1] bg-white p-4 text-left shadow-[0_1px_2px_rgba(14,26,51,0.04)] transition-colors hover:border-[#CFDCF0]"
      >
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E7F6EC] text-[#1FA855]">
          <WhatsAppGlyph />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold text-[#0E1A33]">Chat With Us On WhatsApp</span>
          <span className="block text-xs text-[#5F6878]">Fastest for anything about today's wash.</span>
        </span>
        <ChevronRight className="h-4 w-4 shrink-0 text-[#A3ADBD]" />
      </button>

      {issueSocieties.length > 0 && (
        <button
          type="button"
          onClick={() => setSocietyIssueOpen(true)}
          data-testid="support-society-issue"
          className="flex w-full items-center gap-3.5 rounded-2xl border border-[#E4E9F1] bg-white p-4 text-left shadow-[0_1px_2px_rgba(14,26,51,0.04)] transition-colors hover:border-[#CFDCF0]"
        >
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]">
            <Building2 className="h-5 w-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-semibold text-[#0E1A33]">Society Plan Issue</span>
            <span className="block truncate text-xs text-[#5F6878]">Missed daily wash, captain didn't come, billing — {issueSocieties.map((x) => x.name).join(", ")}</span>
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-[#A3ADBD]" />
        </button>
      )}
      <SocietyIssueModal open={societyIssueOpen} onClose={() => setSocietyIssueOpen(false)} societies={issueSocieties}
        onRaised={() => queryClient.invalidateQueries({ queryKey: ["my-complaints"] })} />

      {isLoading ? (
        <PageLoader />
      ) : isError && !data ? (
        <ErrorState message="Couldn't load your support requests." busy={isFetching} onRetry={() => void refetch()} />
      ) : !tickets.length ? (
        <EmptyState
          icon={LifeBuoy}
          title="No Support Requests"
          description="Something not right with a service? Raise it here."
          action={
            <Button variant="info" onClick={() => setOpen(true)}>
              Raise An Issue
            </Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {tickets.map((t) => (
            <button
              key={t.id}
              onClick={() => setSelected(t)}
              className="flex w-full items-center gap-4 rounded-2xl border border-[#E4E9F1] bg-white p-4 text-left transition-all hover:shadow-[0_8px_24px_rgba(17,24,39,0.08)] sm:p-5"
            >
              <span className="hidden h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0E1A33] sm:flex">
                <MessageSquare className="h-5 w-5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-semibold text-[#0E1A33]">{t.subject}</p>
                </div>
                <p className="mt-0.5 truncate text-xs text-gray-400">
                  {t.category === "society" ? (
                    <span>{[t.society_name, t.registration_number].filter(Boolean).join(" · ")}</span>
                  ) : (
                    <span className="font-mono-num">{t.booking_number || "—"}</span>
                  )}{" "}
                  · raised {format(t.created_at)}
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
        title={selectedFresh?.subject || "Support Request"}
      >
        {selectedFresh && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={selectedFresh.status} />
              <span className="text-xs text-[var(--color-text-secondary)]">
                {selectedFresh.category === "society" ? (
                  <>Society · <span className="font-semibold">{[selectedFresh.society_name, selectedFresh.registration_number].filter(Boolean).join(" · ")}</span></>
                ) : (
                  <>Booking <span className="font-mono-num font-semibold">{selectedFresh.booking_number || "—"}</span></>
                )}{" "}
                · raised {format(selectedFresh.created_at)}
              </span>
            </div>

            <div className="rounded-xl bg-[#F7F9FC] p-3.5">
              <p className="text-xs text-gray-500">Your Message</p>
              <p className="mt-1 text-sm text-[var(--color-text-primary)]">{selectedFresh.description}</p>
            </div>

            <div>
              <p className="text-xs text-gray-500">Conversation</p>
              {selectedFresh.replies?.length ? (
                <div className="mt-2 space-y-2.5">
                  {selectedFresh.replies.map((r, i) => (
                    <div key={i} className={`rounded-xl border p-3 ${r.author_role === "customer" ? "border-[#E4E9F1] bg-[#F7F9FC]" : "border-[#E4E9F1] bg-white"}`}>
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-xs font-bold capitalize text-[#0E1A33]">{r.author_role === "customer" ? "You" : `Blussit ${r.author_role}`}</span>
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
                    className="flex-1 rounded-xl border border-[#E4E9F1] px-3.5 py-2.5 text-sm outline-none focus:border-[#0A66F0]"
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
      <Modal open={open} onClose={() => setOpen(false)} title="Raise A Support Request">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createMutation.mutate();
          }}
        >
          <Select label="Which Booking Is This About?" value={bookingId} onChange={(e) => setBookingId(e.target.value)} required>
            <option value="">Select a booking…</option>
            {/* Arrived from an older booking's "Need help?" that isn't in the recent list. */}
            {bookingId && !(bookings?.data || []).some((b) => b.id === bookingId) && <option value={bookingId}>The booking you came from</option>}
            {(bookings?.data || []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.booking_number} — {format(b.scheduled_date)} · {[bookingTypeName(b), bookingServiceTitle(b)].filter(Boolean).join(" · ")}
              </option>
            ))}
          </Select>
          <Input
            label="What Went Wrong?"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            hint={subject.trim().length > 0 && subject.trim().length < 3 ? "At least 3 characters." : undefined}
            required
          />
          <Input
            label="Tell Us A Bit More"
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
            Submit Request
          </Button>
        </form>
      </Modal>
    </div>
  );
}
