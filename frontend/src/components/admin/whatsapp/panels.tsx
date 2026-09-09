/**
 * WhatsApp CRM secondary views: Templates, Contacts, Campaigns
 * (marketing area), Analytics.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Search } from "lucide-react";
import { Badge, Button, Card, CardBody, CardHeader, Input, Modal, Select, Spinner } from "../../ui";
import { whatsappCrmApi } from "../../../api/admin";
import { getErrorMessage } from "../../../lib/api-client";
import { TemplatePreview } from "./modals";

/* ------------------------------------------------------------------ */
/* Templates                                                           */
/* ------------------------------------------------------------------ */
const STATUS_VARIANT: Record<string, "success" | "warning" | "error" | "info" | "neutral" | "primary"> = {
  APPROVED: "success", PENDING: "warning", REJECTED: "error", PAUSED: "warning", DRAFT: "neutral",
};

export function TemplatesView() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["wa-templates"], queryFn: whatsappCrmApi.templates });
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const sync = useMutation({
    mutationFn: whatsappCrmApi.syncTemplates,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wa-templates"] }),
  });
  const toggle = useMutation({
    mutationFn: ({ name, disabled }: { name: string; disabled: boolean }) => whatsappCrmApi.setTemplateDisabled(name, disabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wa-templates"] }),
  });

  const rows = useMemo(
    () => (data || []).filter((t) => (category === "all" || t.category === category) && (status === "all" || t.status === status)),
    [data, category, status],
  );

  if (isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-2">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">All categories</option>
            <option value="UTILITY">Utility</option>
            <option value="MARKETING">Marketing</option>
            <option value="AUTHENTICATION">Authentication</option>
          </Select>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All statuses</option>
            {Object.keys(STATUS_VARIANT).map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" isLoading={sync.isPending} onClick={() => sync.mutate()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Sync status
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" /> Create template
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((t) => (
          <Card key={t.name} className={t.disabled ? "opacity-60" : ""}>
            <CardBody className="space-y-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-mono-num text-sm font-bold text-[var(--color-text-primary)]">{t.name}</p>
                  <p className="text-[10px] text-[var(--color-text-secondary)]">{t.language} · used {t.usage}×</p>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Badge tone={STATUS_VARIANT[t.status] || "neutral"}>{t.status}</Badge>
                  {t.category && <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${t.category === "MARKETING" ? "bg-purple-50 text-purple-700" : t.category === "AUTHENTICATION" ? "bg-sky-50 text-sky-700" : "bg-gray-100 text-gray-600"}`}>{t.category}</span>}
                </div>
              </div>
              <p className="line-clamp-4 whitespace-pre-wrap rounded-lg bg-gray-50 p-2 text-xs text-gray-700">{t.body}</p>
              {t.rejected_reason && <p className="text-xs text-[var(--color-error)]">Rejected: {t.rejected_reason}</p>}
              <div className="flex justify-end">
                <button type="button" onClick={() => toggle.mutate({ name: t.name, disabled: !t.disabled })} className="text-[11px] font-medium text-[var(--color-text-secondary)] underline hover:text-[var(--color-text-primary)]">
                  {t.disabled ? "Enable" : "Disable"}
                </button>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>
      {rows.length === 0 && <p className="py-10 text-center text-sm text-[var(--color-text-secondary)]">No templates match — try "Sync status".</p>}

      <CreateTemplateModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

function CreateTemplateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ name: "", category: "UTILITY", language: "en_US", body: "", button_text: "", button_url: "https://blussit.com/" });
  const [error, setError] = useState("");
  const create = useMutation({
    mutationFn: () => whatsappCrmApi.createTemplate({ ...form, button_text: form.button_text || undefined, button_url: form.button_text ? form.button_url : undefined }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["wa-templates"] }); setError(""); onClose(); },
    onError: (e) => setError(getErrorMessage(e)),
  });
  return (
    <Modal open={open} onClose={onClose} title="Create template" maxWidth="max-w-xl">
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Input label="Name (lowercase_underscores)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Select label="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
            <option value="UTILITY">Utility (operational)</option>
            <option value="MARKETING">Marketing (needs opt-in)</option>
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">Body — use {"{{1}}"}, {"{{2}}"}… for variables</label>
          <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={5} className="w-full rounded-xl border border-gray-200 p-3 text-sm outline-none focus:border-[var(--color-primary)]" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Input label="Button text (optional)" value={form.button_text} onChange={(e) => setForm({ ...form, button_text: e.target.value })} />
          <Input label="Button URL" value={form.button_url} onChange={(e) => setForm({ ...form, button_url: e.target.value })} />
        </div>
        {form.body && (
          <div>
            <p className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Preview</p>
            <TemplatePreview body={form.body} params={[]} />
          </div>
        )}
        <p className="text-[11px] text-[var(--color-text-secondary)]">
          Submitting sends the template to WhatsApp for review — approval usually takes minutes to a day. Variables can't start or end the message.
        </p>
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button isLoading={create.isPending} disabled={!form.name || form.body.length < 10} onClick={() => create.mutate()}>Submit for review</Button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Contacts                                                            */
/* ------------------------------------------------------------------ */
export function ContactsView({ onOpenChat }: { onOpenChat: (waId: string) => void }) {
  const [search, setSearch] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["wa-contacts", search], queryFn: () => whatsappCrmApi.contacts(search) });
  return (
    <Card>
      <CardHeader>
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search contacts…" className="w-full rounded-xl border border-gray-200 py-2 pl-8 pr-2 text-sm outline-none focus:border-[var(--color-primary)]" />
        </div>
      </CardHeader>
      <CardBody className="overflow-x-auto">
        {isLoading ? <div className="flex justify-center py-10"><Spinner /></div> : (
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="text-xs text-[var(--color-text-secondary)]">
              <tr>
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Phone</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Tags</th>
                <th className="py-2 pr-3 font-medium">Last message</th>
                <th className="py-2 font-medium" />
              </tr>
            </thead>
            <tbody className="text-[var(--color-text-primary)]">
              {(data || []).map((c) => (
                <tr key={c.wa_id} className="border-t border-gray-50">
                  <td className="py-2.5 pr-3 font-medium">{c.name}</td>
                  <td className="py-2.5 pr-3 font-mono-num text-xs">+91 {c.phone}</td>
                  <td className="py-2.5 pr-3 text-xs capitalize">{c.crm_status}</td>
                  <td className="py-2.5 pr-3">
                    <div className="flex flex-wrap gap-1">{c.tags.slice(0, 3).map((t) => <span key={t} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px]">{t}</span>)}</div>
                  </td>
                  <td className="max-w-[16rem] truncate py-2.5 pr-3 text-xs text-[var(--color-text-secondary)]">{c.last_message_text}</td>
                  <td className="py-2.5 text-right">
                    <button type="button" onClick={() => onOpenChat(c.wa_id)} className="text-xs font-semibold text-[var(--color-primary)] underline">Open chat</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!isLoading && (data || []).length === 0 && <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">No contacts yet.</p>}
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Campaigns (future marketing area — deliberately restrained)         */
/* ------------------------------------------------------------------ */
export function CampaignsView() {
  const { data } = useQuery({ queryKey: ["wa-templates"], queryFn: whatsappCrmApi.templates });
  const marketing = (data || []).filter((t) => t.category === "MARKETING");
  return (
    <div className="max-w-2xl space-y-4">
      <Card>
        <CardBody className="space-y-2 p-5">
          <h3 className="text-sm font-bold text-[var(--color-text-primary)]">Marketing campaigns</h3>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Marketing messages are kept strictly separate from operational (utility) messages. They may only be sent to customers with
            marketing consent/opt-in, are billed at WhatsApp's marketing rate, and Meta frequency-caps them per user. Bulk campaign
            sending will be enabled here once the consent flow is live.
          </p>
        </CardBody>
      </Card>
      {marketing.map((t) => (
        <Card key={t.name}>
          <CardBody className="space-y-2 p-4">
            <div className="flex items-center justify-between">
              <p className="font-mono-num text-sm font-bold">{t.name}</p>
              <Badge tone={t.status === "APPROVED" ? "success" : t.status === "DRAFT" ? "neutral" : "warning"}>{t.status}</Badge>
            </div>
            <p className="whitespace-pre-wrap rounded-lg bg-gray-50 p-2 text-xs">{t.body}</p>
            {t.status === "DRAFT" && (
              <p className="text-[11px] text-[var(--color-text-secondary)]">Draft — will be submitted to Meta when the opt-in flow is ready.</p>
            )}
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Analytics                                                           */
/* ------------------------------------------------------------------ */
type WaAnalytics = {
  days: number;
  conversations: { total: number; open: number; pending: number; resolved: number; unread: number };
  messages: { sent: number; received: number };
  templates: { sent: number; delivery_rate_pct: number | null; read_rate_pct: number | null; failure_rate_pct: number | null };
  avg_first_response_minutes: number | null;
  avg_resolution_hours: number | null;
};

export function AnalyticsView() {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useQuery({ queryKey: ["wa-analytics", days], queryFn: () => whatsappCrmApi.analytics(days) });
  if (isLoading || !data) return <div className="flex justify-center py-16"><Spinner /></div>;
  const a = data as unknown as WaAnalytics;
  const pct = (v: number | null) => (v == null ? "—" : `${v}%`);
  const cards = [
    { label: "Conversations", value: a.conversations.total },
    { label: "Open", value: a.conversations.open },
    { label: "Resolved", value: a.conversations.resolved },
    { label: "Unread", value: a.conversations.unread },
    { label: "Messages sent", value: a.messages.sent },
    { label: "Messages received", value: a.messages.received },
    { label: "Template messages", value: a.templates.sent },
    { label: "Delivery rate", value: pct(a.templates.delivery_rate_pct) },
    { label: "Read rate", value: pct(a.templates.read_rate_pct) },
    { label: "Failure rate", value: pct(a.templates.failure_rate_pct) },
    { label: "Avg first response", value: a.avg_first_response_minutes == null ? "—" : `${a.avg_first_response_minutes} min` },
    { label: "Avg resolution", value: a.avg_resolution_hours == null ? "—" : `${a.avg_resolution_hours} h` },
  ];
  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </Select>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {cards.map((c) => (
          <Card key={c.label}>
            <CardBody className="p-4">
              <p className="text-[11px] font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">{c.label}</p>
              <p className="mt-1 font-mono-num text-xl font-bold text-[var(--color-text-primary)]">{c.value}</p>
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}
