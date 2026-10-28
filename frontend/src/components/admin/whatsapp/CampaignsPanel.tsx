/**
 * Admin -> WhatsApp -> Campaigns: festival / broadcast messages.
 * List of campaigns, a three-step New Campaign wizard (Details, Audience,
 * Review & Send) and a live progress page. The audience selection is
 * {mode, filter, excluded_ids, included_ids}: "Select All" is a server-side
 * filter, never a list of every id.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ImagePlus, Megaphone, Plus, Send, TriangleAlert } from "lucide-react";
import { Badge, Button, EmptyState, ErrorState, Input, Panel, Select, Spinner, StatCard } from "../../ui";
import { adminServiceCenterApi } from "../../../api/admin";
import { campaignApi, type AudienceFilter, type Campaign, type CampaignStatus, type Selection } from "../../../api/campaigns";
import { useToast } from "../../../context/ToastContext";
import { getErrorMessage } from "../../../lib/api-client";
import { toTitle } from "../../../lib/titleCase";
import { formatDateTime } from "../../../lib/date";
import { useDebouncedValue } from "../../shared/ListControls";

const STATUS_TONE: Record<CampaignStatus, "neutral" | "warning" | "success" | "error"> = {
  draft: "neutral", queued: "warning", sending: "warning", done: "success", cancelled: "neutral", stopped: "error",
};
const ACTIVE: CampaignStatus[] = ["queued", "sending"];
const FOOTER = "Reply STOP to opt out";
const MAX_CHARS = 600;

const rupees = (paise: number) => `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: paise % 100 ? 2 : 0, maximumFractionDigits: 2 })}`;
const num = (n: number) => n.toLocaleString("en-IN");
function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sec`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}
/** Same single-line rule the server applies (Meta rejects newlines/tabs/4+ spaces in a template parameter). */
const oneLine = (t: string) => t.replace(/[​‌⁠﻿]/g, "").replace(/\s+/g, " ").trim();

const EMPTY_FILTER: AudienceFilter = { search: "" };
const NONE: Selection = { mode: "none", filter: EMPTY_FILTER, excluded_ids: [], included_ids: [] };

export function CampaignsView() {
  const [view, setView] = useState<{ kind: "list" } | { kind: "new" } | { kind: "detail"; id: string }>({ kind: "list" });
  if (view.kind === "new") return <Wizard onClose={() => setView({ kind: "list" })} onSent={(id) => setView({ kind: "detail", id })} />;
  if (view.kind === "detail") return <Progress id={view.id} onBack={() => setView({ kind: "list" })} />;
  return <CampaignList onNew={() => setView({ kind: "new" })} onOpen={(id) => setView({ kind: "detail", id })} />;
}

/* ------------------------------------------------------------------ */
/* List                                                                */
/* ------------------------------------------------------------------ */
function CampaignList({ onNew, onOpen }: { onNew: () => void; onOpen: (id: string) => void }) {
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["wa-campaigns"],
    queryFn: () => campaignApi.list(),
    refetchInterval: (q) => (q.state.data?.items.some((c) => ACTIVE.includes(c.status)) ? 4000 : false),
  });
  const items = data?.items ?? [];
  return (
    <div className="space-y-4">
      <Panel
        title="Campaigns"
        description="Send a festival image and message to many customers in one go. Customers who replied STOP are never included."
        actions={<Button size="sm" onClick={onNew}><Plus className="mr-1 h-4 w-4" /> New Campaign</Button>}
      >
        {isLoading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : isError && !data ? (
          <ErrorState message="Couldn't load campaigns." onRetry={() => void refetch()} busy={isFetching} />
        ) : items.length === 0 ? (
          <EmptyState icon={Megaphone} title="No Campaigns Yet" description="Create your first festival greeting." action={<Button onClick={onNew}>New Campaign</Button>} />
        ) : (
          <ul className="divide-y divide-[var(--color-card-border)]">
            {items.map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => onOpen(c.id)} className="flex w-full flex-col gap-2 py-3 text-left sm:flex-row sm:items-center sm:justify-between">
                  <span className="flex min-w-0 items-center gap-3">
                    <img src={c.image_url} alt="" className="h-12 w-12 shrink-0 rounded-lg border border-[var(--color-card-border)] object-cover" />
                    <span className="min-w-0">
                      <span className="block truncate font-semibold text-[var(--color-text-primary)]">{c.name}</span>
                      <span className="block text-xs text-[var(--color-text-secondary)]">{c.created_at ? formatDateTime(c.created_at) : ""}</span>
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">
                    <Badge tone={STATUS_TONE[c.status]}>{toTitle(c.status)}</Badge>
                    <span className="font-mono-num">{num(c.total_recipients)} Recipients</span>
                    <span className="font-mono-num">{num(c.counts.sent)} Sent</span>
                    <span className="font-mono-num">{num(c.counts.delivered)} Delivered</span>
                    <span className="font-mono-num">{num(c.counts.read)} Read</span>
                    {c.counts.failed > 0 && <span className="font-mono-num text-[var(--color-error)]">{num(c.counts.failed)} Failed</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Message preview (what the customer sees)                            */
/* ------------------------------------------------------------------ */
function PhonePreview({ image, message }: { image?: string | null; message: string }) {
  return (
    <div className="mx-auto w-full max-w-xs rounded-2xl border border-[var(--color-card-border)] bg-[#F3E5B5]/20 p-3">
      <div className="overflow-hidden rounded-xl border border-[var(--color-card-border)] bg-white shadow-sm">
        {image ? (
          <img src={image} alt="Festival" className="aspect-[4/3] w-full object-cover" />
        ) : (
          <div className="flex aspect-[4/3] w-full items-center justify-center bg-gray-100 text-xs text-[var(--color-text-secondary)]">Festival Image</div>
        )}
        <div className="space-y-2 p-3 text-sm text-[var(--color-text-primary)]">
          <p className="whitespace-pre-wrap break-words">
            {"Greetings from Blussit!\n\n"}
            {message || <span className="text-[var(--color-text-secondary)]">Your message appears here</span>}
            {"\n\nWarm wishes, Team Blussit"}
          </p>
          <p className="text-[11px] text-[var(--color-text-secondary)]">{FOOTER}</p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Wizard                                                              */
/* ------------------------------------------------------------------ */
function Steps({ step }: { step: number }) {
  const labels = ["Details", "Audience", "Review & Send"];
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm">
      {labels.map((l, i) => (
        <li key={l} className={`flex items-center gap-2 rounded-full border px-3 py-1 ${step === i + 1 ? "border-[var(--color-primary)] font-semibold text-[var(--color-text-primary)]" : "border-[var(--color-card-border)] text-[var(--color-text-secondary)]"}`}>
          <span className="font-mono-num">{i + 1}</span> {l}
        </li>
      ))}
    </ol>
  );
}

function Wizard({ onClose, onSent }: { onClose: () => void; onSent: (id: string) => void }) {
  const [step, setStep] = useState(1);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [selection, setSelection] = useState<Selection>(NONE);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onClose}><ArrowLeft className="mr-1 h-4 w-4" /> Back To Campaigns</Button>
        <Steps step={step} />
      </div>
      {step === 1 && (
        <DetailsStep
          name={name} setName={setName} message={message} setMessage={setMessage} imageUrl={imageUrl} setImageUrl={setImageUrl}
          existing={campaign} onSaved={(c) => { setCampaign(c); setStep(2); }}
        />
      )}
      {step === 2 && (
        <AudienceStep selection={selection} setSelection={setSelection} onBack={() => setStep(1)} onNext={() => setStep(3)} />
      )}
      {step === 3 && campaign && (
        <ReviewStep campaign={campaign} selection={selection} onBack={() => setStep(2)} onSent={onSent} />
      )}
    </div>
  );
}

function DetailsStep(props: {
  name: string; setName: (v: string) => void; message: string; setMessage: (v: string) => void; imageUrl: string; setImageUrl: (v: string) => void;
  existing: Campaign | null; onSaved: (c: Campaign) => void;
}) {
  const { name, setName, message, setMessage, imageUrl, setImageUrl, existing, onSaved } = props;
  const { push } = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const clean = oneLine(message);
  const tooLong = clean.length > MAX_CHARS;

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!["image/jpeg", "image/png"].includes(file.type)) { push({ tone: "error", title: "Choose a JPG or PNG image." }); return; }
    if (file.size > 5 * 1024 * 1024) { push({ tone: "error", title: "The image must be under 5 MB." }); return; }
    setUploading(true);
    try { setImageUrl(await campaignApi.uploadImage(file)); } catch (e) { push({ tone: "error", title: getErrorMessage(e) }); } finally { setUploading(false); }
  };
  const save = useMutation({
    mutationFn: () => {
      const payload = { name: name.trim(), message: clean, image_url: imageUrl };
      return existing ? campaignApi.update(existing.id, payload) : campaignApi.create(payload);
    },
    onSuccess: onSaved,
    onError: (e) => push({ tone: "error", title: getErrorMessage(e) }),
  });
  const ready = name.trim().length >= 2 && clean.length > 0 && !tooLong && !!imageUrl;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <Panel title="Campaign Details" description="One image and one short message. Customers see it from Blussit with a STOP option.">
        <div className="space-y-4">
          <Input label="Campaign Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Diwali 2026" hint="Only you see this." />
          <div>
            <span className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">Festival Image</span>
            <input ref={fileRef} type="file" accept="image/jpeg,image/png" className="hidden" onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--color-card-border)] px-4 py-6 text-sm text-[var(--color-text-secondary)] hover:border-[var(--color-primary)]"
            >
              {uploading ? <Spinner /> : <ImagePlus className="h-5 w-5" />}
              {imageUrl ? "Replace Image" : "Upload JPG Or PNG (Up To 5 MB)"}
            </button>
          </div>
          <div>
            <label htmlFor="campaign-message" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">Message</label>
            <textarea
              id="campaign-message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={5}
              placeholder="Wishing you and your family a very happy Diwali!"
              className="w-full rounded-xl border border-[#E4E9F1] bg-white px-3 py-2.5 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-primary)] focus:outline-none"
            />
            <p className={`mt-1 flex justify-between text-xs ${tooLong ? "text-[var(--color-error)]" : "text-[var(--color-text-secondary)]"}`}>
              <span>Sent as one line (line breaks become spaces). Emoji are fine.</span>
              <span className="font-mono-num">{clean.length} / {MAX_CHARS}</span>
            </p>
          </div>
          <div className="flex justify-end">
            <Button disabled={!ready} isLoading={save.isPending} onClick={() => save.mutate()}>Continue To Audience</Button>
          </div>
        </div>
      </Panel>
      <div>
        <p className="mb-2 text-center text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">Live Preview</p>
        <PhonePreview image={imageUrl} message={clean} />
      </div>
    </div>
  );
}

/* ------------------------------ audience --------------------------- */
const PAGE_SIZE = 25;

function AudienceStep({ selection, setSelection, onBack, onNext }: {
  selection: Selection; setSelection: (s: Selection) => void; onBack: () => void; onNext: () => void;
}) {
  const [draft, setDraft] = useState<AudienceFilter>({ search: "" });
  const [page, setPage] = useState(1);
  const search = useDebouncedValue(draft.search ?? "", 350);
  const filter = useMemo<AudienceFilter>(() => ({ ...draft, search }), [draft, search]);
  const filterKey = JSON.stringify(filter);
  const lastKey = useRef(filterKey);
  const { data: centers } = useQuery({ queryKey: ["wa-campaign-centers"], queryFn: () => adminServiceCenterApi.list({ page_size: 100 }).then((r) => r.data) });

  // Changing a filter starts a fresh selection: "all" always means "all matching THIS filter".
  useEffect(() => {
    if (lastKey.current === filterKey) return;
    lastKey.current = filterKey;
    setPage(1);
    setSelection({ mode: "none", filter, excluded_ids: [], included_ids: [] });
  }, [filterKey, filter, setSelection]);

  const effective: Selection = { ...selection, filter };
  const selKey = JSON.stringify([selection.mode, selection.excluded_ids, selection.included_ids]);
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["wa-campaign-audience", filterKey, page, selKey],
    queryFn: () => campaignApi.audience(effective, page, PAGE_SIZE),
    placeholderData: keepPreviousData,
  });
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rows = data?.items ?? [];
  const selected = data?.selected_count ?? 0;
  const tooMany = selection.excluded_ids.length > 20000 || selection.included_ids.length > 20000;

  const toggle = (id: string, on: boolean) => {
    const set = (list: string[], add: boolean) => (add ? Array.from(new Set([...list, id])) : list.filter((x) => x !== id));
    if (selection.mode === "all") setSelection({ ...selection, filter, excluded_ids: set(selection.excluded_ids, !on) });
    else setSelection({ ...selection, mode: "none", filter, included_ids: set(selection.included_ids, on) });
  };
  const togglePage = (on: boolean) => {
    const ids = rows.map((r) => r.id);
    if (selection.mode === "all") {
      setSelection({ ...selection, filter, excluded_ids: on ? selection.excluded_ids.filter((x) => !ids.includes(x)) : Array.from(new Set([...selection.excluded_ids, ...ids])) });
    } else {
      setSelection({ ...selection, mode: "none", filter, included_ids: on ? Array.from(new Set([...selection.included_ids, ...ids])) : selection.included_ids.filter((x) => !ids.includes(x)) });
    }
  };
  const allOnPage = rows.length > 0 && rows.every((r) => r.selected);
  const set = <K extends keyof AudienceFilter>(k: K, v: AudienceFilter[K]) => setDraft((d) => ({ ...d, [k]: v }));

  return (
    <div className="space-y-4">
      <Panel title="Filters" description="Narrow the list, then select. Only customers who can receive marketing are shown.">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Input label="Search" value={draft.search ?? ""} onChange={(e) => set("search", e.target.value)} placeholder="Name Or Phone" />
          <Select label="Center" value={draft.center_id ?? ""} onChange={(e) => set("center_id", e.target.value || null)}>
            <option value="">All Centers</option>
            {(centers ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
          <Input label="City" value={draft.city ?? ""} onChange={(e) => set("city", e.target.value || null)} placeholder="Indore" />
          <Select label="Active Plan" value={draft.has_active_plan == null ? "" : draft.has_active_plan ? "yes" : "no"} onChange={(e) => set("has_active_plan", e.target.value === "" ? null : e.target.value === "yes")}>
            <option value="">Any</option>
            <option value="yes">Has An Active Plan</option>
            <option value="no">No Active Plan</option>
          </Select>
          <Input label="Last Booking From" type="date" value={draft.last_booking_from ?? ""} onChange={(e) => set("last_booking_from", e.target.value || null)} />
          <Input label="Last Booking To" type="date" value={draft.last_booking_to ?? ""} onChange={(e) => set("last_booking_to", e.target.value || null)} />
          <Input label="Bookings At Least" type="number" min={0} value={draft.bookings_min ?? ""} onChange={(e) => set("bookings_min", e.target.value === "" ? null : Number(e.target.value))} />
          <Input label="Bookings At Most" type="number" min={0} value={draft.bookings_max ?? ""} onChange={(e) => set("bookings_max", e.target.value === "" ? null : Number(e.target.value))} />
        </div>
        <label className="mt-3 inline-flex min-h-11 items-center gap-2 text-sm text-[var(--color-text-primary)] sm:min-h-0">
          <input type="checkbox" checked={!!draft.no_booking} onChange={(e) => set("no_booking", e.target.checked ? true : null)} className="h-4 w-4 accent-black" />
          Never Booked
        </label>
      </Panel>

      <Panel
        title="Customers"
        actions={
          <>
            <span className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]" aria-live="polite">Selected: {num(selected)} of {num(total)}</span>
            <Button size="sm" variant="outline" disabled={total === 0} onClick={() => setSelection({ mode: "all", filter, excluded_ids: [], included_ids: [] })}>Select All ({num(total)})</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelection({ mode: "none", filter, excluded_ids: [], included_ids: [] })}>Clear</Button>
          </>
        }
      >
        {tooMany && <p className="mb-3 flex items-center gap-2 text-sm text-[var(--color-error)]"><TriangleAlert className="h-4 w-4" /> Too many hand-picked changes - use Select All or Clear and narrow the filters.</p>}
        {isLoading ? (
          <div className="flex justify-center py-10"><Spinner /></div>
        ) : isError && !data ? (
          <ErrorState message="Couldn't load customers." onRetry={() => void refetch()} busy={isFetching} />
        ) : rows.length === 0 ? (
          <EmptyState title="No Customers Match" description="Loosen a filter." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-[var(--color-text-secondary)]">
                <tr>
                  <th className="w-10 py-2"><input type="checkbox" aria-label="Select this page" checked={allOnPage} onChange={(e) => togglePage(e.target.checked)} className="h-4 w-4 accent-black" /></th>
                  <th className="py-2">Customer</th><th className="py-2">Phone</th><th className="py-2">City</th><th className="py-2">Center</th>
                  <th className="py-2 text-right">Bookings</th><th className="py-2">Last Booking</th><th className="py-2">Plan</th>
                </tr>
              </thead>
              <tbody className={`divide-y divide-[var(--color-card-border)] ${isFetching ? "opacity-60" : ""}`}>
                {rows.map((r) => (
                  <tr key={r.id} className={r.selected ? "bg-[var(--color-primary-light)]" : ""}>
                    <td className="py-2"><input type="checkbox" aria-label={`Select ${r.name}`} checked={r.selected} onChange={(e) => toggle(r.id, e.target.checked)} className="h-4 w-4 accent-black" /></td>
                    <td className="py-2 font-medium text-[var(--color-text-primary)]">{r.name || "—"}</td>
                    <td className="font-mono-num py-2">{r.phone}</td>
                    <td className="py-2">{r.city || "—"}</td>
                    <td className="py-2">{r.center || "—"}</td>
                    <td className="font-mono-num py-2 text-right">{r.bookings_count}</td>
                    <td className="py-2">{r.last_booking_at ? formatDateTime(r.last_booking_at).split(",")[0] : "Never"}</td>
                    <td className="py-2">{r.has_active_plan ? <Badge tone="success">Active</Badge> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-4 flex items-center justify-between gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">Page {page} of {num(pages)}</span>
          <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </div>
      </Panel>

      <div className="flex justify-between gap-2">
        <Button variant="outline" onClick={onBack}>Back</Button>
        <Button disabled={selected === 0 || tooMany} onClick={onNext}>Review ({num(selected)} Selected)</Button>
      </div>
    </div>
  );
}

/* ------------------------------ review ----------------------------- */
function ReviewStep({ campaign, selection, onBack, onSent }: { campaign: Campaign; selection: Selection; onBack: () => void; onSent: (id: string) => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const [typed, setTyped] = useState("");
  const { data: q, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["wa-campaign-preview", campaign.id, JSON.stringify(selection)],
    queryFn: () => campaignApi.preview(campaign.id, selection),
    staleTime: 0,
  });
  const test = useMutation({
    mutationFn: () => campaignApi.test(campaign.id),
    onSuccess: (r) => push({ tone: "success", title: `Test message sent to ${r.sent_to}.` }),
    onError: (e) => push({ tone: "error", title: getErrorMessage(e) }),
  });
  const send = useMutation({
    mutationFn: () => campaignApi.send(campaign.id, selection, q!.recipient_count),
    onSuccess: (c) => { void qc.invalidateQueries({ queryKey: ["wa-campaigns"] }); push({ tone: "success", title: "Campaign queued. Sending has started." }); onSent(c.id); },
    onError: (e) => { push({ tone: "error", title: getErrorMessage(e) }); void refetch(); },
  });
  if (isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (isError || !q) return <ErrorState message="Couldn't prepare the review." onRetry={() => void refetch()} busy={isFetching} />;
  const confirmed = typed.trim() === String(q.recipient_count);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Recipients" value={num(q.recipient_count)} />
          <StatCard label="Estimated Cost" value={rupees(q.cost_paise)} hint={`${rupees(q.cost_per_message_paise)} per message`} />
          <StatCard label="Estimated Time" value={duration(q.eta_seconds)} hint={`${q.rate_per_second} messages per second`} />
          <StatCard label="Sent In Last 24 Hours" value={num(q.sent_last_24h)} hint={`Daily limit ${num(q.daily_cap)}`} />
        </div>
        <Panel title="Before You Send">
          <ul className="space-y-2 text-sm text-[var(--color-text-primary)]">
            <li>Template: <span className="font-mono-num">{q.template.name}</span> <Badge tone={q.template.ready ? "success" : "error"}>{toTitle(q.template.status.toLowerCase())}</Badge></li>
            <li className="text-[var(--color-text-secondary)]">{q.consent_note}</li>
            <li className="text-[var(--color-text-secondary)]">Meta bills each marketing message and may skip people who have received too many promotions.</li>
          </ul>
          {q.problems.length > 0 && (
            <ul className="mt-3 space-y-1 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-[var(--color-error)]" role="alert">
              {q.problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" isLoading={test.isPending} disabled={!q.template.ready} onClick={() => test.mutate()}>Send Test To My Number</Button>
          </div>
          <div className="mt-5 border-t border-[var(--color-card-border)] pt-4">
            <Input
              label={`Type ${q.recipient_count} To Confirm`}
              inputMode="numeric"
              value={typed}
              onChange={(e) => setTyped(e.target.value.replace(/\D/g, ""))}
              hint="This sends a real WhatsApp message to every selected customer and cannot be undone. You can cancel the unsent ones."
            />
            <div className="mt-4 flex justify-between gap-2">
              <Button variant="outline" onClick={onBack}>Back</Button>
              <Button disabled={!confirmed || !q.can_send} isLoading={send.isPending} onClick={() => send.mutate()}>
                <Send className="mr-1.5 h-4 w-4" /> Send Campaign
              </Button>
            </div>
          </div>
        </Panel>
      </div>
      <div>
        <p className="mb-2 text-center text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">What Customers Will See</p>
        <PhonePreview image={q.image_url} message={q.message} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Progress                                                            */
/* ------------------------------------------------------------------ */
const REASON_LABEL: Record<string, string> = {
  not_on_whatsapp: "Not On WhatsApp", invalid_number: "Invalid Number", opted_out: "Opted Out", inactive: "Inactive Account",
  staff_number: "Staff Number", cancelled: "Cancelled", campaign_stopped: "Campaign Stopped", not_delivered_by_meta: "Held Back By Meta",
  undelivered: "Not Delivered", rejected: "Rejected By Meta", transport: "Network Error",
};

function Progress({ id, onBack }: { id: string; onBack: () => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const { data: c, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["wa-campaign", id],
    queryFn: () => campaignApi.get(id),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 2500 : false),
  });
  const [failFilter, setFailFilter] = useState("failed");
  const rec = useQuery({ queryKey: ["wa-campaign-recipients", id, failFilter, c?.counts.failed, c?.counts.skipped], queryFn: () => campaignApi.recipients(id, failFilter), enabled: !!c && c.status !== "draft" });
  const cancel = useMutation({
    mutationFn: () => campaignApi.cancel(id),
    onSuccess: () => { push({ tone: "success", title: "Campaign cancelled. Unsent messages will not go out." }); void qc.invalidateQueries({ queryKey: ["wa-campaign", id] }); void qc.invalidateQueries({ queryKey: ["wa-campaigns"] }); },
    onError: (e) => push({ tone: "error", title: getErrorMessage(e) }),
  });
  if (isLoading) return <div className="flex justify-center py-16"><Spinner /></div>;
  if (isError || !c) return <ErrorState message="Couldn't load this campaign." onRetry={() => void refetch()} busy={isFetching} />;
  const done = c.counts.sent + c.counts.failed + c.counts.skipped;
  const pct = c.total_recipients ? Math.min(100, Math.round((done / c.total_recipients) * 100)) : 0;
  const active = ACTIVE.includes(c.status);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="mr-1 h-4 w-4" /> Back To Campaigns</Button>
        {active && <Button variant="danger" size="sm" isLoading={cancel.isPending} onClick={() => { if (window.confirm("Cancel this campaign? Messages not yet sent will not go out.")) cancel.mutate(); }}>Cancel Campaign</Button>}
      </div>
      <Panel title={c.name} description={c.created_at ? `Created ${formatDateTime(c.created_at)}` : undefined} actions={<Badge tone={STATUS_TONE[c.status]}>{toTitle(c.status)}</Badge>}>
        <div className="grid gap-5 lg:grid-cols-[1fr_16rem]">
          <div className="space-y-4">
            <div>
              <div className="h-2 overflow-hidden rounded-full bg-gray-100" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <div className="h-full rounded-full bg-[var(--color-primary)] transition-all" style={{ width: `${pct}%` }} />
              </div>
              <p className="mt-1 font-mono-num text-xs text-[var(--color-text-secondary)]">{num(done)} of {num(c.total_recipients)} handled ({pct}%) - Estimated cost {rupees(c.cost_estimate_paise)}</p>
            </div>
            {c.waiting_reason === "daily_cap" && <p className="text-sm text-amber-700">Paused at the daily sending limit. It continues by itself as the 24-hour window frees up.</p>}
            {c.status === "stopped" && c.stop_reason && <p className="flex items-start gap-2 text-sm text-[var(--color-error)]" role="alert"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> Stopped: {c.stop_reason}. Unsent messages were skipped.</p>}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <StatCard label="Waiting" value={num(c.counts.queued)} />
              <StatCard label="Sent" value={num(c.counts.sent)} />
              <StatCard label="Delivered" value={num(c.counts.delivered)} />
              <StatCard label="Read" value={num(c.counts.read)} />
              <StatCard label="Failed" value={num(c.counts.failed)} tone={c.counts.failed ? "error" : "default"} />
              <StatCard label="Skipped" value={num(c.counts.skipped)} />
            </div>
          </div>
          <PhonePreview image={c.image_url} message={c.message} />
        </div>
      </Panel>
      {c.status !== "draft" && (c.counts.failed > 0 || c.counts.skipped > 0) && (
        <Panel
          title="Problems"
          description={Object.entries(c.failure_reasons ?? {}).map(([k, v]) => `${REASON_LABEL[k] ?? toTitle(k)}: ${num(v)}`).join(" - ")}
          actions={
            <Select compact value={failFilter} onChange={(e) => setFailFilter(e.target.value)}>
              <option value="failed">Failed</option>
              <option value="skipped">Skipped</option>
            </Select>
          }
        >
          {rec.isLoading ? <Spinner /> : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-[var(--color-text-secondary)]"><tr><th className="py-2">Phone</th><th className="py-2">Reason</th><th className="py-2">Detail</th></tr></thead>
                <tbody className="divide-y divide-[var(--color-card-border)]">
                  {(rec.data?.items ?? []).map((r) => (
                    <tr key={r.id}><td className="font-mono-num py-2">{r.phone}</td><td className="py-2">{REASON_LABEL[r.reason ?? ""] ?? toTitle(r.reason ?? "unknown")}</td><td className="py-2 text-[var(--color-text-secondary)]">{r.error || "—"}</td></tr>
                  ))}
                </tbody>
              </table>
              {(rec.data?.total ?? 0) > 25 && <p className="mt-2 text-xs text-[var(--color-text-secondary)]">Showing the first 25 of {num(rec.data!.total)}.</p>}
            </div>
          )}
        </Panel>
      )}
    </div>
  );
}
