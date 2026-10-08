/**
 * WhatsApp CRM secondary views: Templates, Contacts, Campaigns
 * (marketing area), Analytics.
 */
import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle, Info, Plus, RefreshCw, Rocket, Search, Send } from "lucide-react";
import { Badge, Button, Card, CardBody, CardHeader, DataTable, ErrorState, Input, Modal, Select, Spinner, type Column } from "../../ui";
import { whatsappCrmApi, type WaCatalogueTemplate, type WaConfigWarning } from "../../../api/admin";
import { useToast } from "../../../context/ToastContext";
import { getErrorMessage } from "../../../lib/api-client";
import { toTitle } from "../../../lib/titleCase";
import { formatDateTime } from "../../../lib/date";
import { TemplatePreview } from "./modals";
import { useDebouncedValue } from "../../shared/ListControls";

/* ------------------------------------------------------------------ */
/* Templates                                                           */
/* ------------------------------------------------------------------ */
const STATUS_VARIANT: Record<string, "success" | "warning" | "error" | "info" | "neutral" | "primary"> = {
  APPROVED: "success", PENDING: "warning", REJECTED: "error", PAUSED: "warning", DRAFT: "neutral",
};

/** Templates: the app's own catalogue (what each event sends — Submit to
 *  Meta from here) and everything on the WhatsApp account. */
export function TemplatesView() {
  const [view, setView] = useState<"catalogue" | "live">("catalogue");
  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-xl border border-[var(--color-card-border)] bg-white p-1" role="tablist">
        {(
          [
            { key: "catalogue", label: "App Templates" },
            { key: "live", label: "On WhatsApp" },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={view === t.key}
            onClick={() => setView(t.key)}
            className={`min-h-11 rounded-lg px-3.5 text-sm font-semibold transition-colors sm:min-h-9 ${
              view === t.key ? "bg-[var(--color-primary)] text-white" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-primary-light)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {view === "catalogue" ? <TemplateCatalogue /> : <LiveTemplatesView />}
    </div>
  );
}

const CATALOGUE_STATUS_LABEL: Record<string, string> = {
  APPROVED: "Approved",
  PENDING: "In Review",
  IN_APPEAL: "In Appeal",
  REJECTED: "Rejected",
  PAUSED: "Paused",
  DISABLED: "Disabled",
  NOT_SUBMITTED: "Not Submitted",
};

/** Why Submit is off for this row (null = it can be submitted). */
function submitBlockedReason(t: WaCatalogueTemplate): string | null {
  if (!t.can_submit) {
    if (t.status === "APPROVED") return "Already approved";
    if (t.status === "PENDING" || t.status === "IN_APPEAL") return "Waiting for WhatsApp's review";
    return "Already on WhatsApp";
  }
  if (t.button_text && !t.button_url) return "Set the Google review URL first (Settings)";
  return null;
}

function TemplateCatalogue() {
  const qc = useQueryClient();
  const { push: pushToast } = useToast();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["wa-template-catalogue"], queryFn: whatsappCrmApi.templateCatalogue });
  const submit = useMutation({
    mutationFn: (key: string) => whatsappCrmApi.submitCatalogueTemplate(key),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["wa-template-catalogue"] });
      qc.invalidateQueries({ queryKey: ["wa-templates"] });
      pushToast({ tone: "success", title: "Submitted For Review", message: `${r.name} — usually approved within minutes to a day.` });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Submit", message: getErrorMessage(err) }),
  });

  const rows = useMemo(() => (data || []).map((t) => ({ ...t, id: t.key })), [data]);
  type Row = (typeof rows)[number];

  // Each cell once — the md+ table and the phone cards render the same parts.
  const nameCell = (t: Row) => (
    <div className="min-w-0">
      <p className="break-all font-mono-num text-xs font-bold">{t.live_name || t.name}</p>
      {t.events?.length ? <p className="mt-0.5 text-[11px] text-[var(--color-text-secondary)]">{t.events.map((e) => toTitle(e)).join(", ")}</p> : null}
    </div>
  );
  const categoryCell = (t: Row) => (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${t.category === "MARKETING" ? "bg-purple-50 text-purple-700" : "bg-gray-100 text-gray-600"}`}>
      {toTitle(t.category.toLowerCase())}
    </span>
  );
  const statusCell = (t: Row) => (
    <div className="space-y-0.5">
      <Badge tone={STATUS_VARIANT[t.status] || (t.status === "NOT_SUBMITTED" ? "neutral" : "warning")}>
        {CATALOGUE_STATUS_LABEL[t.status] || toTitle(t.status.toLowerCase())}
      </Badge>
      {t.meta_category && t.meta_category !== t.category && (
        <p className="text-[11px] text-amber-700">Filed As {toTitle(t.meta_category.toLowerCase())}</p>
      )}
      {t.rejected_reason && <p className="text-[11px] text-[var(--color-error)]">{t.rejected_reason}</p>}
    </div>
  );
  const buttonCell = (t: Row) =>
    t.button_text ? (
      <div className="text-xs">
        <p className="font-semibold">{t.button_text}</p>
        <p className="break-all text-[var(--color-text-secondary)]">{t.button_url || "No URL set"}</p>
      </div>
    ) : null;
  const submitCell = (t: Row, align: "end" | "start") => {
    const blocked = submitBlockedReason(t);
    return (
      <div className={`flex flex-col gap-1 ${align === "end" ? "min-w-[140px] items-end text-right" : "items-stretch"}`}>
        <Button
          size="sm"
          disabled={!!blocked || (submit.isPending && submit.variables !== t.key)}
          isLoading={submit.isPending && submit.variables === t.key}
          onClick={() => submit.mutate(t.key)}
          title={blocked || (t.submit_name ? `Submits as ${t.submit_name}` : undefined)}
        >
          <Send className="h-3.5 w-3.5" /> {t.status === "REJECTED" ? "Resubmit" : "Submit"}
        </Button>
        {blocked ? (
          <p className="text-[11px] text-[var(--color-text-secondary)]">{blocked}</p>
        ) : t.submit_name && t.submit_name !== t.name ? (
          <p className="font-mono-num text-[11px] text-[var(--color-text-secondary)]">As {t.submit_name}</p>
        ) : null}
      </div>
    );
  };

  const columns: Column<Row>[] = [
    { header: "Template", accessor: (t) => <div className="max-w-[220px]">{nameCell(t)}</div> },
    { header: "Category", accessor: categoryCell },
    { header: "Status", accessor: (t) => <div className="max-w-[180px]">{statusCell(t)}</div> },
    {
      header: "Message",
      accessor: (t) => <p className="line-clamp-3 max-w-[320px] whitespace-pre-wrap text-xs text-gray-700">{t.body}</p>,
    },
    { header: "Button", accessor: (t) => <div className="max-w-[180px]">{buttonCell(t) ?? <span className="text-gray-300">—</span>}</div> },
    { header: "", accessor: (t) => submitCell(t, "end") },
  ];

  if (isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (isError && !data) return <ErrorState message="Couldn't load the template catalogue." onRetry={() => void refetch()} busy={isFetching} />;
  return (
    <div className="space-y-2">
      <p className="text-sm text-[var(--color-text-secondary)]">
        Every template the app sends. Submit one to WhatsApp for review — a rejected one goes in as its next version.
      </p>
      {/* Phones: one stacked card per template (the table squeezed names to
          a few characters a line and cut the message off at 390 px). */}
      <ul className="space-y-3 md:hidden" data-testid="wa-template-cards">
        {rows.length === 0 ? (
          <li className="rounded-2xl border border-[var(--color-card-border)] bg-white p-4 text-sm text-[var(--color-text-secondary)]">No Templates</li>
        ) : (
          rows.map((t) => (
            <li key={t.id} className="space-y-3 rounded-2xl border border-[var(--color-card-border)] bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                {nameCell(t)}
                <div className="shrink-0">{categoryCell(t)}</div>
              </div>
              {statusCell(t)}
              <p className="whitespace-pre-wrap break-words text-xs text-gray-700">{t.body}</p>
              {buttonCell(t)}
              {submitCell(t, "start")}
            </li>
          ))
        )}
      </ul>
      <div className="hidden md:block">
        <DataTable columns={columns} data={rows} emptyTitle="No Templates" />
      </div>
    </div>
  );
}

/** Admin WhatsApp settings: the Google review link (the review-request
 *  template's button; empty = review requests stop). */
export function WhatsAppSettingsView() {
  const qc = useQueryClient();
  const { push: pushToast } = useToast();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["wa-settings"], queryFn: whatsappCrmApi.settings });
  const [url, setUrl] = useState<string | null>(null);
  const value = url ?? data?.google_review_url ?? "";
  const trimmed = value.trim();
  const valid = trimmed === "" || /^https:\/\/[^\s]+\.[^\s]+$/i.test(trimmed);
  const save = useMutation({
    mutationFn: () => whatsappCrmApi.updateSettings({ google_review_url: trimmed }),
    onSuccess: (r) => {
      qc.setQueryData(["wa-settings"], r);
      qc.invalidateQueries({ queryKey: ["wa-template-catalogue"] });
      setUrl(null);
      pushToast({ tone: "success", title: "Settings Saved", message: r.google_review_url ? undefined : "Review requests are off until a link is set." });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Save", message: getErrorMessage(err) }),
  });
  if (isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (isError && !data) return <ErrorState message="Couldn't load WhatsApp settings." onRetry={() => void refetch()} busy={isFetching} />;
  return (
    <Card className="max-w-xl">
      <CardHeader>
        <h3 className="font-semibold text-[var(--color-text-primary)]">Google Review Link</h3>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
          The button on the review request customers get about 2 hours after a paid, completed wash. Leave it empty to stop review requests.
        </p>
      </CardHeader>
      <CardBody className="space-y-3">
        <Input
          label="Google Review URL"
          type="url"
          inputMode="url"
          value={value}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://g.page/r/…/review"
          error={!valid ? "Use a full https:// link." : undefined}
        />
        <div className="flex justify-end">
          <Button className="min-h-11" isLoading={save.isPending} disabled={!valid || trimmed === (data?.google_review_url ?? "")} onClick={() => save.mutate()}>
            Save
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

function LiveTemplatesView() {
  const qc = useQueryClient();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["wa-templates"], queryFn: () => whatsappCrmApi.templates() });
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState("all");
  const [createOpen, setCreateOpen] = useState(false);
  const sync = useMutation({
    mutationFn: whatsappCrmApi.syncTemplates,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wa-templates"] }),
  });
  const bootstrap = useMutation({
    mutationFn: whatsappCrmApi.bootstrapTemplates,
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
  if (isError && !data) return <ErrorState message="Couldn't load templates." onRetry={() => void refetch()} busy={isFetching} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-2">
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">All Categories</option>
            <option value="UTILITY">Utility</option>
            <option value="MARKETING">Marketing</option>
            <option value="AUTHENTICATION">Authentication</option>
          </Select>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All Statuses</option>
            {Object.keys(STATUS_VARIANT).map((s) => <option key={s} value={s}>{toTitle(s.toLowerCase())}</option>)}
          </Select>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" isLoading={sync.isPending} onClick={() => sync.mutate()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Sync Status
          </Button>
          <Button
            variant="secondary"
            isLoading={bootstrap.isPending}
            onClick={() => bootstrap.mutate()}
            title="Submits every standard BLUSSIT template not already on WhatsApp yet, including the ones whose button links straight to the specific booking"
          >
            <Rocket className="mr-1.5 h-3.5 w-3.5" /> Submit Missing Templates
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" /> Create Template
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
                  <Badge tone={STATUS_VARIANT[t.status] || "neutral"}>{toTitle(t.status.toLowerCase())}</Badge>
                  {t.category && <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${t.category === "MARKETING" ? "bg-purple-50 text-purple-700" : t.category === "AUTHENTICATION" ? "bg-sky-50 text-sky-700" : "bg-gray-100 text-gray-600"}`}>{toTitle(t.category.toLowerCase())}</span>}
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
      {rows.length === 0 && <p className="py-10 text-center text-sm text-[var(--color-text-secondary)]">No templates match — try "Sync Status".</p>}

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
    <Modal open={open} onClose={onClose} title="Create Template" maxWidth="max-w-xl">
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Input label="Name (lowercase_underscores)" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Select label="Category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
            <option value="UTILITY">Utility (Operational)</option>
            <option value="MARKETING">Marketing (Needs Opt-In)</option>
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">Body — Use {"{{1}}"}, {"{{2}}"}… For Variables</label>
          <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={5} className="w-full rounded-xl border border-gray-200 p-3 text-sm outline-none focus:border-[var(--color-primary)]" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Input label="Button Text (Optional)" value={form.button_text} onChange={(e) => setForm({ ...form, button_text: e.target.value })} />
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
          <Button isLoading={create.isPending} disabled={!form.name || form.body.length < 10} onClick={() => create.mutate()}>Submit For Review</Button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Contacts                                                            */
/* ------------------------------------------------------------------ */
const CONTACTS_PAGE = 50;

export function ContactsView({ onOpenChat }: { onOpenChat: (waId: string) => void }) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search.trim(), 350);
  const { data: pages, isLoading, isError, isFetching, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ["wa-contacts", debouncedSearch],
    queryFn: ({ pageParam }) => whatsappCrmApi.contacts(debouncedSearch, pageParam, CONTACTS_PAGE),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.length < CONTACTS_PAGE ? undefined : last[last.length - 1]?.last_message_at || undefined),
  });
  const data = pages?.pages.flat();
  return (
    <Card>
      <CardHeader>
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search contacts…" className="w-full rounded-xl border border-gray-200 py-2 pl-8 pr-2 text-sm outline-none focus:border-[var(--color-primary)]" />
        </div>
      </CardHeader>
      <CardBody className="overflow-x-auto">
        {isLoading ? <div className="flex justify-center py-10"><Spinner /></div> : isError && !pages ? (
          <ErrorState message="Couldn't load contacts." onRetry={() => void refetch()} busy={isFetching} className="p-4" />
        ) : (
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="text-xs text-[var(--color-text-secondary)]">
              <tr>
                <th className="py-2 pr-3 font-medium">Name</th>
                <th className="py-2 pr-3 font-medium">Phone</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Tags</th>
                <th className="py-2 pr-3 font-medium">Last Message</th>
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
                    <div className="flex flex-wrap gap-1">{c.tags.slice(0, 3).map((t) => <span key={t} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px]">{toTitle(t)}</span>)}</div>
                  </td>
                  <td className="max-w-[16rem] truncate py-2.5 pr-3 text-xs text-[var(--color-text-secondary)]">{c.last_message_text}</td>
                  <td className="py-2.5 text-right">
                    <button type="button" onClick={() => onOpenChat(c.wa_id)} className="text-xs font-semibold text-[var(--color-primary)] underline">Open Chat</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!isLoading && !(isError && !pages) && (data || []).length === 0 && <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">No contacts yet.</p>}
        {hasNextPage && (
          <div className="pt-3 text-center">
            <Button size="sm" variant="outline" isLoading={isFetchingNextPage} onClick={() => fetchNextPage()}>
              Load More
            </Button>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/* Campaigns (future marketing area — deliberately restrained)         */
/* ------------------------------------------------------------------ */
export function CampaignsView() {
  const { data, isError, isFetching, refetch } = useQuery({ queryKey: ["wa-templates"], queryFn: () => whatsappCrmApi.templates() });
  const marketing = (data || []).filter((t) => t.category === "MARKETING");
  return (
    <div className="max-w-2xl space-y-4">
      <Card>
        <CardBody className="space-y-2 p-5">
          <h3 className="text-sm font-bold text-[var(--color-text-primary)]">Marketing Campaigns</h3>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Marketing messages are kept strictly separate from operational (utility) messages. They may only be sent to customers with
            marketing consent/opt-in, are billed at WhatsApp's marketing rate, and Meta frequency-caps them per user. Bulk campaign
            sending will be enabled here once the consent flow is live.
          </p>
        </CardBody>
      </Card>
      {isError && !data && <ErrorState message="Couldn't load marketing templates." onRetry={() => void refetch()} busy={isFetching} />}
      {marketing.map((t) => (
        <Card key={t.name}>
          <CardBody className="space-y-2 p-4">
            <div className="flex items-center justify-between">
              <p className="font-mono-num text-sm font-bold">{t.name}</p>
              <Badge tone={t.status === "APPROVED" ? "success" : t.status === "DRAFT" ? "neutral" : "warning"}>{toTitle(t.status.toLowerCase())}</Badge>
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
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["wa-analytics", days], queryFn: () => whatsappCrmApi.analytics(days) });
  // The period picker stays up whatever happens below it.
  const picker = (
    <div className="flex justify-end">
      <Select value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
        <option value="7">Last 7 Days</option>
        <option value="30">Last 30 Days</option>
        <option value="90">Last 90 Days</option>
      </Select>
    </div>
  );
  if (isError && !data)
    return (
      <div className="space-y-4">
        {picker}
        <ErrorState message="Couldn't load WhatsApp analytics." onRetry={() => void refetch()} busy={isFetching} />
      </div>
    );
  if (isLoading || !data) return <div className="flex justify-center py-16"><Spinner /></div>;
  const a = data as unknown as WaAnalytics;
  const pct = (v: number | null) => (v == null ? "—" : `${v}%`);
  const cards = [
    { label: "Conversations", value: a.conversations.total },
    { label: "Open", value: a.conversations.open },
    { label: "Resolved", value: a.conversations.resolved },
    { label: "Unread", value: a.conversations.unread },
    { label: "Messages Sent", value: a.messages.sent },
    { label: "Messages Received", value: a.messages.received },
    { label: "Template Messages", value: a.templates.sent },
    { label: "Delivery Rate", value: pct(a.templates.delivery_rate_pct) },
    { label: "Read Rate", value: pct(a.templates.read_rate_pct) },
    { label: "Failure Rate", value: pct(a.templates.failure_rate_pct) },
    { label: "Avg First Response", value: a.avg_first_response_minutes == null ? "—" : `${a.avg_first_response_minutes} min` },
    { label: "Avg Resolution", value: a.avg_resolution_hours == null ? "—" : `${a.avg_resolution_hours} h` },
  ];
  return (
    <div className="space-y-4">
      {picker}
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
      <DeliveryHealthCard days={days} />
    </div>
  );
}

const FAILURE_LABELS: Record<string, string> = {
  no_template: "No Approved Template",
  window_closed: "Outside 24-Hour Window",
  transport: "Network / Meta Error",
  rejected: "Rejected By Meta",
  opted_out: "Customer Opted Out",
  no_phone: "No Phone Number",
};

/** What did NOT reach people — the queue by status, failures by reason,
 *  and the latest failures (phones masked by the server). */
function DeliveryHealthCard({ days }: { days: number }) {
  const qc = useQueryClient();
  const key = ["wa-delivery-health", days];
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: key,
    queryFn: () => whatsappCrmApi.deliveryHealth(Math.min(days, 90)),
  });
  // Refresh skips the server's short cache (?fresh=1).
  const refresh = useMutation({
    mutationFn: () => whatsappCrmApi.deliveryHealth(Math.min(days, 90), true),
    onSuccess: (fresh) => qc.setQueryData(key, fresh),
  });
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-semibold text-[var(--color-text-primary)]">Delivery Health</h3>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">WhatsApp messages that didn&apos;t reach people in the last {days} days.</p>
          </div>
          <Button size="sm" variant="secondary" isLoading={refresh.isPending} onClick={() => refresh.mutate()}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" /> Refresh
          </Button>
        </div>
      </CardHeader>
      <CardBody>
        {isLoading ? (
          <Spinner />
        ) : isError && !data ? (
          <ErrorState className="p-4" message="Couldn't load delivery health." onRetry={() => void refetch()} busy={isFetching} />
        ) : data ? (
          <div className="space-y-4" data-testid="wa-delivery-health">
            <ConfigWarnings warnings={data.config_warnings} />
            {/* Queue statuses as notification_service keeps them: sending =
                first try in flight, pending = waiting to retry, failed =
                refused for good, dead = gave up after retries, undelivered =
                Meta accepted it, then reported it not delivered. */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                { label: "Sent", value: data.queue.sent ?? 0 },
                { label: "Sending", value: data.queue.sending ?? 0 },
                { label: "Retrying", value: data.queue.pending ?? 0, warn: true },
                { label: "Failed", value: data.queue.failed ?? 0, warn: true },
                { label: "Undelivered", value: data.queue.undelivered ?? 0, warn: true },
                { label: "Dead", value: data.queue.dead ?? 0, warn: true },
                { label: "Refused (No Template / Window)", value: data.refused_outside_window ?? 0, warn: true },
              ].map((c) => (
                <div key={c.label} className="min-w-0 rounded-xl border border-[var(--color-card-border)] p-3">
                  <p className="text-[11px] font-medium text-[var(--color-text-secondary)]">{c.label}</p>
                  <p className={`mt-0.5 font-mono-num text-lg font-bold ${c.warn && c.value > 0 ? "text-amber-700" : "text-[var(--color-text-primary)]"}`}>{c.value}</p>
                </div>
              ))}
            </div>
            {Object.keys(data.failures_by_reason || {}).length > 0 && (
              <div className="flex flex-wrap gap-2">
                {Object.entries(data.failures_by_reason).map(([reason, n]) => (
                  <Badge key={reason} tone="warning">
                    {FAILURE_LABELS[reason] || toTitle(reason)} · {n}
                  </Badge>
                ))}
              </div>
            )}
            {data.recent_failures?.length ? (
              <div>
                <p className="mb-2 text-xs font-semibold text-[var(--color-text-secondary)]">Latest Failures</p>
                <ul className="divide-y divide-[var(--ui-row-line,#EEF2F7)] rounded-xl border border-[var(--color-card-border)]">
                  {data.recent_failures.slice(0, 10).map((f) => (
                    <li key={f.id} className="flex flex-wrap items-start justify-between gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
                      <span className="min-w-0 break-words">
                        <span className="font-medium text-[var(--color-text-primary)]">{f.title || toTitle(f.event) || "Message"}</span>
                        <span className="text-[var(--color-text-secondary)]">
                          {f.phone ? ` · ${f.phone}` : ""} · {FAILURE_LABELS[f.failure || ""] || toTitle(f.failure) || toTitle(f.status)}
                        </span>
                        {f.error && <span className="block text-xs text-[var(--color-text-secondary)]">{f.error}</span>}
                      </span>
                      <span className="shrink-0 font-mono-num text-xs text-[var(--color-text-secondary)]">{f.at ? formatDateTime(f.at) : ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-[var(--color-text-secondary)]">No failed messages in this period.</p>
            )}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}

const WARNING_STYLE: Record<WaConfigWarning["severity"], { box: string; icon: typeof AlertCircle; label: string }> = {
  error: { box: "border-red-200 bg-red-50 text-red-800", icon: AlertCircle, label: "Fix Now" },
  warning: { box: "border-amber-200 bg-amber-50 text-amber-900", icon: AlertTriangle, label: "Check" },
  info: { box: "border-[var(--color-card-border)] bg-gray-50 text-[var(--color-text-primary)]", icon: Info, label: "Note" },
};

/** Set-up problems that stop WhatsApp reaching people — worst first. */
function ConfigWarnings({ warnings }: { warnings?: WaConfigWarning[] }) {
  if (!warnings?.length) return null;
  const order = { error: 0, warning: 1, info: 2 } as const;
  const sorted = [...warnings].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3));
  return (
    <ul className="space-y-2" data-testid="wa-config-warnings">
      {sorted.map((w, i) => {
        const style = WARNING_STYLE[w.severity] || WARNING_STYLE.info;
        const Icon = style.icon;
        return (
          <li key={`${w.code}-${w.user_id || i}`} className={`flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-sm ${style.box}`}>
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">
              <span className="font-semibold">{style.label}: </span>
              {w.message}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
