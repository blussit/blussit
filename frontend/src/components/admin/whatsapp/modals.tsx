/**
 * WhatsApp CRM modals: template sending (with live preview) and starting
 * a brand-new conversation. Business-initiated messages MUST be approved
 * templates — free-form first messages aren't offered anywhere here.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Modal, Select } from "../../ui";
import { whatsappCrmApi, type WaTemplate } from "../../../api/admin";
import { getErrorMessage } from "../../../lib/api-client";

export function fillTemplate(body: string, params: string[]): string {
  return body.replace(/\{\{(\d+)\}\}/g, (_, n) => params[Number(n) - 1] || `{{${n}}}`);
}

export function TemplatePreview({ body, params }: { body: string; params: string[] }) {
  return (
    <div className="rounded-xl bg-[#e7f8ef] p-3 text-sm whitespace-pre-wrap text-gray-800 shadow-inner">
      {fillTemplate(body, params)}
    </div>
  );
}

function useApprovedTemplates() {
  const { data } = useQuery({ queryKey: ["wa-templates"], queryFn: whatsappCrmApi.templates });
  return (data || []).filter((t) => t.status === "APPROVED" && !t.disabled);
}

export function TemplateForm({
  onSend,
  isSending,
  error,
  sendLabel = "Send template",
}: {
  onSend: (template: WaTemplate, params: string[]) => void;
  isSending: boolean;
  error: string;
  sendLabel?: string;
}) {
  const templates = useApprovedTemplates();
  const [name, setName] = useState("");
  const [params, setParams] = useState<string[]>([]);
  const selected = useMemo(() => templates.find((t) => t.name === name), [templates, name]);

  return (
    <div className="space-y-3">
      <Select label="Approved template" value={name} onChange={(e) => { setName(e.target.value); setParams([]); }}>
        <option value="">Select…</option>
        {templates.map((t) => (
          <option key={t.name} value={t.name}>{t.name} ({t.category?.toLowerCase()})</option>
        ))}
      </Select>
      {selected && (
        <>
          {Array.from({ length: selected.param_count }).map((_, i) => (
            <Input
              key={i}
              label={`Variable {{${i + 1}}}`}
              value={params[i] || ""}
              onChange={(e) => setParams((p) => { const next = [...p]; next[i] = e.target.value; return next; })}
            />
          ))}
          <div>
            <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Preview</p>
            <TemplatePreview body={selected.body} params={params} />
          </div>
        </>
      )}
      {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
      <Button
        className="w-full"
        disabled={!selected || Array.from({ length: selected?.param_count || 0 }).some((_, i) => !(params[i] || "").trim())}
        isLoading={isSending}
        onClick={() => selected && onSend(selected, params.slice(0, selected.param_count))}
      >
        {sendLabel}
      </Button>
      {templates.length === 0 && (
        <p className="text-xs text-[var(--color-text-secondary)]">No approved templates yet — check the Templates tab and sync approval status.</p>
      )}
    </div>
  );
}

export function SendTemplateModal({ waId, open, onClose }: { waId: string; open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [error, setError] = useState("");
  const send = useMutation({
    mutationFn: ({ name, params }: { name: string; params: string[] }) => whatsappCrmApi.sendTemplate(waId, name, params),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["wa-thread", waId] }); setError(""); onClose(); },
    onError: (e) => setError(getErrorMessage(e)),
  });
  return (
    <Modal open={open} onClose={onClose} title="Send approved template">
      <TemplateForm error={error} isSending={send.isPending} onSend={(t, params) => send.mutate({ name: t.name, params })} />
    </Modal>
  );
}

export function NewConversationModal({ open, onClose, onStarted }: { open: boolean; onClose: () => void; onStarted: (waId: string) => void }) {
  const qc = useQueryClient();
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const start = useMutation({
    mutationFn: ({ name, params }: { name: string; params: string[] }) => whatsappCrmApi.startConversation(phone, name, params),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["wa-conversations"] });
      setError("");
      onClose();
      onStarted(data.wa_id);
    },
    onError: (e) => setError(getErrorMessage(e)),
  });
  return (
    <Modal open={open} onClose={onClose} title="New conversation">
      <div className="space-y-3">
        <Input label="WhatsApp number" placeholder="10-digit number, e.g. 9876543210" value={phone} onChange={(e) => setPhone(e.target.value)} />
        <p className="text-xs text-[var(--color-text-secondary)]">
          First contact must be an approved template — WhatsApp does not allow free-form messages to customers who haven't messaged you.
        </p>
        <TemplateForm
          error={error}
          isSending={start.isPending}
          sendLabel="Send message"
          onSend={(t, params) => {
            if (phone.replace(/\D/g, "").length < 10) { setError("Enter a valid phone number"); return; }
            start.mutate({ name: t.name, params });
          }}
        />
      </div>
    </Modal>
  );
}
