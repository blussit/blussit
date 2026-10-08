import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, MessageCircle, XCircle } from "lucide-react";
import { staffMessagingApi, type UniversalMessageResult } from "../../api/crm";
import { getErrorMessage } from "../../lib/api-client";
import { Button, Modal } from "../ui";

/**
 * Staff → one customer on WhatsApp (manager: their center's customers;
 * admin: any). It goes out through the approved "universal message"
 * template — "Hi {name}, {message} — Team Blussit" — so it works outside
 * the 24-hour window too. The result says whether it was sent, and why not.
 */

const MAX = 500;

const FAILURE_TEXT: Record<string, string> = {
  no_template: "The message template isn't approved on WhatsApp yet.",
  opted_out: "The customer turned off WhatsApp messages.",
  no_recipient: "The customer has no WhatsApp number.",
  no_phone: "The customer has no phone number.",
  business_number: "That number is our own business number.",
  auth: "WhatsApp refused our credentials — tell the admin.",
  rejected: "WhatsApp rejected the message.",
  transport: "Couldn't reach WhatsApp — it will retry.",
  undelivered: "WhatsApp accepted it but couldn't deliver it.",
  window_closed: "The 24-hour window is closed and no template was approved.",
};

function outcome(r: UniversalMessageResult): { ok: boolean; title: string; detail: string } {
  if (r.status === "sent") return { ok: true, title: "Sent On WhatsApp", detail: "The customer will see it in their WhatsApp chat with us." };
  if (r.status === "pending" || r.status === "sending" || r.status === "failed")
    return {
      ok: false,
      title: r.status === "failed" ? "Not Sent Yet — Retrying" : "Queued",
      detail: (r.failure && FAILURE_TEXT[r.failure]) || r.error || "It will go out shortly.",
    };
  return {
    ok: false,
    title: "Not Delivered",
    detail: (r.failure && FAILURE_TEXT[r.failure]) || r.error || "WhatsApp didn't take the message.",
  };
}

export function SendWhatsAppMessageDialog({
  open,
  onClose,
  customerId,
  customerName,
}: {
  open: boolean;
  onClose: () => void;
  customerId: string;
  customerName?: string | null;
}) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<UniversalMessageResult | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setText("");
    setResult(null);
    setError("");
  }, [open]);

  const first = (customerName || "").trim().split(/\s+/)[0] || "there";
  const trimmed = text.trim();
  const send = useMutation({
    mutationFn: () => staffMessagingApi.sendUniversalMessage(customerId, trimmed),
    onSuccess: ({ result }) => setResult(result),
    onError: (err) => setError(getErrorMessage(err)),
  });

  const shown = result ? outcome(result) : null;
  return (
    <Modal open={open} onClose={onClose} title="Send WhatsApp Message" maxWidth="max-w-md">
      {shown ? (
        <div className="space-y-4" data-testid="wa-message-result">
          <div className={`flex items-start gap-3 rounded-xl px-3.5 py-3 ${shown.ok ? "bg-[var(--ui-success-bg,#E7F6EC)]" : "bg-amber-50"}`}>
            {shown.ok ? (
              <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-success)]" />
            ) : (
              <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
            )}
            <div>
              <p className="text-sm font-semibold text-[#0E1A33]">{shown.title}</p>
              <p className="text-sm text-[var(--color-text-secondary)]">{shown.detail}</p>
            </div>
          </div>
          <div className="flex gap-2">
            {!shown.ok && (
              <Button variant="outline" className="min-h-11 flex-1" onClick={() => setResult(null)}>
                Edit And Retry
              </Button>
            )}
            <Button className="min-h-11 flex-1" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <label htmlFor="wa-universal-message" className="mb-1.5 block text-sm font-medium text-[#0E1A33]">
              Message
            </label>
            <textarea
              id="wa-universal-message"
              value={text}
              maxLength={MAX}
              rows={4}
              autoFocus
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. your captain is running 15 minutes late today"
              className="w-full rounded-xl border border-[var(--color-card-border)] p-3 text-sm outline-none focus:border-[var(--color-primary)]"
            />
            <p className="mt-1 text-right font-mono-num text-xs text-gray-400">
              {text.length}/{MAX}
            </p>
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold text-gray-500">Preview</p>
            <p className="whitespace-pre-wrap break-words rounded-xl bg-gray-50 px-3.5 py-2.5 text-sm text-[#0E1A33]">
              <MessageCircle className="mr-1.5 inline h-3.5 w-3.5 text-gray-400" />
              Hi {first}, {trimmed || "…"} — Team Blussit
            </p>
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button className="min-h-11 flex-1" isLoading={send.isPending} disabled={!trimmed} onClick={() => { setError(""); send.mutate(); }}>
              Send
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
