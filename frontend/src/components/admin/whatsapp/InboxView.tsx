/**
 * WhatsApp Inbox — 3-column CRM layout (conversations / chat / customer
 * context). Tablet collapses the right panel into a drawer; mobile
 * navigates list -> chat. Free-text sending is gated by the 24h window
 * (and enforced again server-side); outside it only approved templates
 * can go out.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Bot, Check, CheckCheck, ChevronDown, CircleAlert, FileText,
  Info, MapPin, MessageSquarePlus, Paperclip, Search, Send, User, X,
} from "lucide-react";
import { Badge, Button, Spinner } from "../../ui";
import { whatsappCrmApi, type WaConversation, type WaMessage } from "../../../api/admin";
import { getErrorMessage } from "../../../lib/api-client";
import { NewConversationModal, SendTemplateModal } from "./modals";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "open", label: "Open" },
  { key: "pending", label: "Pending" },
  { key: "resolved", label: "Resolved" },
  { key: "mine", label: "Assigned to me" },
  { key: "booking", label: "Booking related" },
  { key: "complaint", label: "Complaint" },
  { key: "new_customer", label: "New customer" },
];

function timeLabel(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function StatusTicks({ status }: { status: WaMessage["status"] }) {
  if (status === "FAILED") return <CircleAlert className="h-3.5 w-3.5 text-[var(--color-error)]" />;
  if (status === "READ") return <CheckCheck className="h-3.5 w-3.5 text-sky-500" />;
  if (status === "DELIVERED") return <CheckCheck className="h-3.5 w-3.5 opacity-60" />;
  if (status === "SENT") return <Check className="h-3.5 w-3.5 opacity-60" />;
  return null;
}

export function InboxView() {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const [showProfile, setShowProfile] = useState(false);
  const [newOpen, setNewOpen] = useState(false);

  const { data: conversations, isLoading } = useQuery({
    queryKey: ["wa-conversations", filter, search],
    queryFn: () => whatsappCrmApi.conversations(filter, search),
    refetchInterval: 8000,
  });

  const activeConvo = useMemo(() => (conversations || []).find((c) => c.wa_id === active) || null, [conversations, active]);

  return (
    <div className="flex h-[calc(100vh-11rem)] min-h-[480px] overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-[var(--shadow-soft)]">
      {/* LEFT: conversation list */}
      <div className={`flex w-full shrink-0 flex-col border-r border-gray-100 md:w-80 ${active ? "hidden md:flex" : "flex"}`}>
        <div className="space-y-2 border-b border-gray-100 p-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search name, phone, BK id, vehicle…"
                className="w-full rounded-xl border border-gray-200 py-2 pl-8 pr-2 text-sm outline-none focus:border-[var(--color-primary)]"
              />
            </div>
            <button
              type="button"
              title="New conversation"
              onClick={() => setNewOpen(true)}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-primary)] text-white hover:opacity-90"
            >
              <MessageSquarePlus className="h-4 w-4" />
            </button>
          </div>
          <div className="flex gap-1 overflow-x-auto pb-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors ${filter === f.key ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading && <div className="flex justify-center py-10"><Spinner /></div>}
          {!isLoading && (conversations || []).length === 0 && (
            <p className="px-4 py-10 text-center text-sm text-[var(--color-text-secondary)]">No conversations found.</p>
          )}
          {(conversations || []).map((c) => (
            <button
              key={c.wa_id}
              type="button"
              onClick={() => setActive(c.wa_id)}
              className={`block w-full border-b border-gray-50 px-3 py-2.5 text-left transition-colors hover:bg-gray-50 ${active === c.wa_id ? "bg-[var(--color-primary-light)]/40" : ""}`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-sm font-semibold text-[var(--color-text-primary)]">{c.name}</span>
                <span className="shrink-0 text-[10px] text-gray-400">{timeLabel(c.last_message_at)}</span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-xs text-[var(--color-text-secondary)]">
                  {c.last_message_direction === "out" && "↩ "}
                  {c.last_message_text || "…"}
                </span>
                {c.unread_count > 0 && (
                  <span className="inline-flex min-w-[1.1rem] shrink-0 items-center justify-center rounded-full bg-[var(--color-success)] px-1 text-[10px] font-bold text-white">
                    {c.unread_count}
                  </span>
                )}
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                <StatusBadge status={c.crm_status} />
                {c.has_active_booking && <span className="rounded-full bg-sky-50 px-1.5 py-0.5 text-[9px] font-semibold text-sky-700">BOOKING</span>}
                {c.bot_paused && <span className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700">AGENT</span>}
                {c.tags.slice(0, 2).map((t) => (
                  <span key={t} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[9px] font-medium text-gray-600">{t}</span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* CENTER: chat */}
      <div className={`min-w-0 flex-1 flex-col ${active ? "flex" : "hidden md:flex"}`}>
        {active ? (
          <ChatWindow
            waId={active}
            convo={activeConvo}
            onBack={() => setActive(null)}
            onToggleProfile={() => setShowProfile((v) => !v)}
          />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-[var(--color-text-secondary)]">
            <MessageSquarePlus className="h-8 w-8 opacity-40" />
            <p className="text-sm">Select a conversation to start</p>
          </div>
        )}
      </div>

      {/* RIGHT: customer context (xl inline; below xl a drawer) */}
      {active && (
        <div className={`${showProfile ? "flex" : "hidden xl:flex"} w-full max-w-xs shrink-0 flex-col border-l border-gray-100 bg-white max-xl:absolute max-xl:inset-y-0 max-xl:right-0 max-xl:z-20 max-xl:shadow-2xl`}>
          <CustomerPanel waId={active} onClose={() => setShowProfile(false)} />
        </div>
      )}

      <NewConversationModal open={newOpen} onClose={() => setNewOpen(false)} onStarted={(waId) => setActive(waId)} />
    </div>
  );
}

function StatusBadge({ status }: { status: WaConversation["crm_status"] }) {
  const map = {
    open: "bg-green-50 text-green-700",
    pending: "bg-amber-50 text-amber-700",
    resolved: "bg-gray-100 text-gray-500",
  } as const;
  return <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase ${map[status] || map.open}`}>{status}</span>;
}

/* ------------------------------------------------------------------ */
/* Chat window                                                         */
/* ------------------------------------------------------------------ */
function ChatWindow({ waId, convo, onBack, onToggleProfile }: { waId: string; convo: WaConversation | null; onBack: () => void; onToggleProfile: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [templateOpen, setTemplateOpen] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["wa-thread", waId],
    queryFn: () => whatsappCrmApi.thread(waId),
    refetchInterval: 5000,
  });
  const conversation = data?.conversation || convo;
  const windowActive = conversation?.window?.active ?? false;

  useEffect(() => {
    whatsappCrmApi.markRead(waId).then(() => qc.invalidateQueries({ queryKey: ["wa-conversations"] }));
  }, [waId, qc, data?.messages?.length]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "auto" });
  }, [data?.messages?.length, waId]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["wa-thread", waId] });
    qc.invalidateQueries({ queryKey: ["wa-conversations"] });
  };

  const sendText = useMutation({
    mutationFn: () => whatsappCrmApi.sendText(waId, text),
    onSuccess: () => { setText(""); setError(""); invalidate(); },
    onError: (e) => setError(getErrorMessage(e)),
  });

  const sendFile = useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await whatsappCrmApi.uploadMedia(file);
      await whatsappCrmApi.sendMedia(waId, { media_type: uploaded.media_type, media_id: uploaded.media_id, caption: text, filename: uploaded.filename || file.name });
    },
    onSuccess: () => { setPendingFile(null); setText(""); setError(""); invalidate(); },
    onError: (e) => setError(getErrorMessage(e)),
  });

  // Group messages by day for date separators.
  const groups = useMemo(() => {
    const out: { day: string; messages: WaMessage[] }[] = [];
    for (const m of data?.messages || []) {
      const day = m.at ? new Date(m.at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "";
      if (!out.length || out[out.length - 1].day !== day) out.push({ day, messages: [] });
      out[out.length - 1].messages.push(m);
    }
    return out;
  }, [data?.messages]);

  return (
    <>
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-100 px-3 py-2.5">
        <button type="button" onClick={onBack} className="rounded-lg p-1.5 hover:bg-gray-100 md:hidden"><ArrowLeft className="h-4 w-4" /></button>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{conversation?.name}</p>
          <p className="text-[11px] text-[var(--color-text-secondary)]">+91 {conversation?.phone}</p>
        </div>
        <HeaderActions waId={waId} conversation={conversation} onToggleProfile={onToggleProfile} />
      </div>

      {/* Window banner */}
      <div className={`flex items-center gap-1.5 px-3 py-1 text-[11px] font-medium ${windowActive ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-800"}`}>
        <span>{windowActive ? "🟢 Customer service window active — free replies allowed" : "🟡 Window expired — only approved templates can be sent"}</span>
      </div>

      {/* Messages */}
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto bg-[#f6f4ef] px-3 py-3">
        {isLoading && <div className="flex justify-center py-10"><Spinner /></div>}
        {groups.map((g) => (
          <div key={g.day}>
            <div className="my-2 flex justify-center">
              <span className="rounded-full bg-white px-2.5 py-0.5 text-[10px] font-medium text-gray-500 shadow-sm">{g.day}</span>
            </div>
            {g.messages.map((m) => <Bubble key={m.id} m={m} />)}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-t border-gray-100 p-2.5">
        {error && <p className="mb-1.5 flex items-center gap-1 text-xs text-[var(--color-error)]"><CircleAlert className="h-3.5 w-3.5" /> {error}</p>}
        {pendingFile && (
          <div className="mb-1.5 flex items-center gap-2 rounded-lg bg-gray-50 px-2.5 py-1.5 text-xs">
            <Paperclip className="h-3.5 w-3.5" />
            <span className="min-w-0 flex-1 truncate">{pendingFile.name} · {(pendingFile.size / 1024).toFixed(0)} KB</span>
            <button type="button" onClick={() => setPendingFile(null)}><X className="h-3.5 w-3.5" /></button>
          </div>
        )}
        {windowActive ? (
          <div className="flex items-end gap-2">
            <input
              ref={fileRef}
              type="file"
              hidden
              accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,audio/mpeg,audio/ogg,.doc,.docx"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) setPendingFile(f); e.target.value = ""; }}
            />
            <button type="button" title="Attach" onClick={() => fileRef.current?.click()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-gray-200 text-gray-500 hover:bg-gray-50">
              <Paperclip className="h-4 w-4" />
            </button>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (pendingFile) sendFile.mutate(pendingFile); else if (text.trim()) sendText.mutate(); } }}
              placeholder={pendingFile ? "Caption (optional)…" : "Type a reply…"}
              rows={1}
              className="max-h-28 min-h-[2.25rem] flex-1 resize-y rounded-xl border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[var(--color-primary)]"
            />
            <Button
              className="!h-9 !px-3"
              isLoading={sendText.isPending || sendFile.isPending}
              onClick={() => (pendingFile ? sendFile.mutate(pendingFile) : text.trim() && sendText.mutate())}
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        ) : (
          <Button className="w-full" variant="secondary" onClick={() => setTemplateOpen(true)}>
            Select an approved template to start the conversation
          </Button>
        )}
      </div>
      <SendTemplateModal waId={waId} open={templateOpen} onClose={() => setTemplateOpen(false)} />
    </>
  );
}

function HeaderActions({ waId, conversation, onToggleProfile }: { waId: string; conversation: WaConversation | null; onToggleProfile: () => void }) {
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const { data: agents } = useQuery({ queryKey: ["wa-agents"], queryFn: whatsappCrmApi.agents, staleTime: 300000 });
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["wa-thread", waId] });
    qc.invalidateQueries({ queryKey: ["wa-conversations"] });
  };
  const setStatus = useMutation({ mutationFn: (s: string) => whatsappCrmApi.setStatus(waId, s), onSuccess: invalidate });
  const assign = useMutation({ mutationFn: (uid: string | null) => whatsappCrmApi.assign(waId, uid), onSuccess: invalidate });
  const toggleBot = useMutation({ mutationFn: (p: boolean) => whatsappCrmApi.setBotPaused(waId, p), onSuccess: invalidate });

  return (
    <div className="relative flex items-center gap-1">
      <select
        title="Assign conversation"
        value={conversation?.assigned_to || ""}
        onChange={(e) => assign.mutate(e.target.value || null)}
        className="hidden max-w-[9rem] rounded-lg border border-gray-200 px-1.5 py-1 text-[11px] text-gray-600 outline-none sm:block"
      >
        <option value="">Unassigned</option>
        {(agents || []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
      {conversation?.crm_status !== "resolved" ? (
        <button type="button" onClick={() => setStatus.mutate("resolved")} className="hidden rounded-lg bg-green-50 px-2 py-1 text-[11px] font-semibold text-green-700 hover:bg-green-100 sm:block">
          Resolve
        </button>
      ) : (
        <button type="button" onClick={() => setStatus.mutate("open")} className="hidden rounded-lg bg-gray-100 px-2 py-1 text-[11px] font-semibold text-gray-600 hover:bg-gray-200 sm:block">
          Reopen
        </button>
      )}
      <button type="button" title="Customer details" onClick={onToggleProfile} className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100">
        <User className="h-4 w-4" />
      </button>
      <button type="button" title="More" onClick={() => setMenuOpen((v) => !v)} className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100">
        <ChevronDown className="h-4 w-4" />
      </button>
      {menuOpen && (
        <div className="absolute right-0 top-9 z-30 w-48 rounded-xl border border-gray-100 bg-white p-1 shadow-lg" onMouseLeave={() => setMenuOpen(false)}>
          <MenuBtn onClick={() => { setStatus.mutate("pending"); setMenuOpen(false); }}>Mark as pending</MenuBtn>
          <MenuBtn onClick={() => { setStatus.mutate("open"); setMenuOpen(false); }}>Mark as open</MenuBtn>
          <MenuBtn onClick={() => { toggleBot.mutate(!conversation?.bot_paused); setMenuOpen(false); }}>
            {conversation?.bot_paused ? "Hand back to booking bot" : "Pause booking bot here"}
          </MenuBtn>
        </div>
      )}
    </div>
  );
}

function MenuBtn({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="block w-full rounded-lg px-2.5 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50">
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Message bubble                                                      */
/* ------------------------------------------------------------------ */
function Bubble({ m }: { m: WaMessage }) {
  const mine = m.direction === "out";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"} py-0.5`}>
      <div className={`max-w-[78%] rounded-2xl px-3 py-1.5 text-sm shadow-sm ${mine ? "rounded-br-sm bg-[#dcf3c8]" : "rounded-bl-sm bg-white"}`}>
        {mine && (
          <p className="mb-0.5 flex items-center gap-1 text-[10px] font-semibold text-gray-500">
            {m.automated ? <Bot className="h-3 w-3" /> : <User className="h-3 w-3" />}
            {m.automated ? "Automated · BLUSSIT" : m.sender}
            {m.template_name && <span className="rounded bg-black/5 px-1 font-normal">{m.template_name}</span>}
          </p>
        )}
        <MessageBody m={m} />
        <p className="mt-0.5 flex items-center justify-end gap-1 text-[9px] text-gray-400">
          {m.at ? new Date(m.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : ""}
          {mine && <StatusTicks status={m.status} />}
        </p>
        {m.status === "FAILED" && m.errors?.length ? (
          <p className="text-[10px] text-[var(--color-error)]">Failed: {m.errors[0].title || m.errors[0].message}</p>
        ) : null}
      </div>
    </div>
  );
}

function MessageBody({ m }: { m: WaMessage }) {
  if (m.type === "image" && m.media_id) {
    return (
      <a href={whatsappCrmApi.mediaUrl(m.media_id)} target="_blank" rel="noreferrer">
        <img src={whatsappCrmApi.mediaUrl(m.media_id)} alt="attachment" className="max-h-56 rounded-lg" loading="lazy" />
        {m.text && !m.text.startsWith("📷") && <p className="mt-1 whitespace-pre-wrap">{m.text}</p>}
      </a>
    );
  }
  if ((m.type === "document" || m.media_type === "document") && m.media_id) {
    return (
      <a href={whatsappCrmApi.mediaUrl(m.media_id)} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg bg-black/5 px-2 py-1.5">
        <FileText className="h-4 w-4 shrink-0" />
        <span className="truncate text-xs font-medium">{m.filename || "Document"}</span>
      </a>
    );
  }
  if ((m.type === "video" || m.type === "audio") && m.media_id) {
    const Tag = m.type === "video" ? "video" : "audio";
    return <Tag src={whatsappCrmApi.mediaUrl(m.media_id)} controls className="max-h-56 max-w-full rounded-lg" />;
  }
  if (m.type === "location" && m.latitude != null) {
    return (
      <a href={`https://maps.google.com/?q=${m.latitude},${m.longitude}`} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-sky-700 underline">
        <MapPin className="h-3.5 w-3.5" /> Shared location
      </a>
    );
  }
  if (m.type === "redacted") {
    return <p className="italic text-gray-400">{m.text}</p>;
  }
  return <p className="whitespace-pre-wrap break-words">{m.text}</p>;
}

/* ------------------------------------------------------------------ */
/* Customer panel                                                      */
/* ------------------------------------------------------------------ */
function CustomerPanel({ waId, onClose }: { waId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["wa-profile", waId], queryFn: () => whatsappCrmApi.contactProfile(waId) });
  const { data: defaultTags } = useQuery({ queryKey: ["wa-default-tags"], queryFn: whatsappCrmApi.defaultTags, staleTime: 600000 });
  const setTags = useMutation({
    mutationFn: (tags: string[]) => whatsappCrmApi.setTags(waId, tags),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["wa-profile", waId] }); qc.invalidateQueries({ queryKey: ["wa-conversations"] }); },
  });

  if (isLoading || !data) return <div className="flex flex-1 items-center justify-center"><Spinner /></div>;
  const c = data.customer;
  const s = data.stats;
  const tags = data.conversation.tags;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <p className="text-xs font-bold uppercase tracking-wide text-[var(--color-text-secondary)]">Customer</p>
        <button type="button" onClick={onClose} className="rounded-lg p-1 hover:bg-gray-100 xl:hidden"><X className="h-4 w-4" /></button>
      </div>
      <div className="space-y-4 p-4">
        <div>
          <p className="text-sm font-bold text-[var(--color-text-primary)]">{c?.name || data.conversation.name}</p>
          <p className="text-xs text-[var(--color-text-secondary)]">+91 {data.conversation.phone}</p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {s?.is_repeat ? <Badge tone="success">Repeat customer</Badge> : c ? <Badge tone="info">Customer</Badge> : <Badge tone="warning">No account</Badge>}
            {c && !c.phone_verified && <Badge tone="warning">Unverified</Badge>}
          </div>
        </div>

        {data.vehicles.length > 0 && (
          <PanelSection title="Vehicle">
            {data.vehicles.map((v, i) => (
              <p key={i} className="text-xs text-[var(--color-text-primary)]">
                {v.brand} {v.model} {v.type ? `· ${v.type}` : ""}
                <span className="block font-mono-num text-[10px] text-[var(--color-text-secondary)]">{v.registration_number}</span>
              </p>
            ))}
          </PanelSection>
        )}

        {data.current_booking && (
          <PanelSection title="Current booking">
            <p className="text-xs font-semibold text-[var(--color-text-primary)]">{data.current_booking.booking_number} · {data.current_booking.status.replace(/_/g, " ")}</p>
            <p className="text-xs text-[var(--color-text-secondary)]">{data.current_booking.services.join(", ")}</p>
            <p className="text-xs text-[var(--color-text-secondary)]">{data.current_booking.date} · {data.current_booking.slot}</p>
            {data.current_booking.captain && <p className="text-xs text-[var(--color-text-secondary)]">Captain: {data.current_booking.captain}</p>}
            <p className="text-xs font-semibold text-[var(--color-text-primary)]">₹{data.current_booking.amount}</p>
          </PanelSection>
        )}

        {s && (
          <PanelSection title="History">
            <div className="grid grid-cols-2 gap-2">
              <Fact label="Bookings" value={String(s.total_bookings)} />
              <Fact label="Completed" value={String(s.completed)} />
              <Fact label="Cancelled" value={String(s.cancelled)} />
              <Fact label="Last service" value={s.last_service || "—"} />
              <Fact label="Lifetime value" value={`₹${s.lifetime_value.toLocaleString("en-IN")}`} />
              <Fact label="Avg rating" value={s.avg_rating_given ? `${s.avg_rating_given} ★` : "—"} />
            </div>
          </PanelSection>
        )}

        <PanelSection title="Tags">
          <div className="flex flex-wrap gap-1">
            {(defaultTags?.tags || []).map((t) => {
              const on = tags.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTags.mutate(on ? tags.filter((x) => x !== t) : [...tags, t])}
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${on ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}
                >
                  {t}
                </button>
              );
            })}
          </div>
        </PanelSection>

        {c && (
          <div className="space-y-1.5">
            <a href={`/admin/users?search=${encodeURIComponent(c.phone)}`} className="block rounded-xl border border-gray-200 px-3 py-2 text-center text-xs font-semibold text-[var(--color-text-primary)] hover:bg-gray-50">View customer</a>
            <a href={`/admin/bookings?search=${encodeURIComponent(c.phone)}`} className="block rounded-xl border border-gray-200 px-3 py-2 text-center text-xs font-semibold text-[var(--color-text-primary)] hover:bg-gray-50">View booking history</a>
          </div>
        )}
        {data.conversation.assigned_to_name && (
          <p className="flex items-center gap-1 text-[11px] text-[var(--color-text-secondary)]"><Info className="h-3 w-3" /> Assigned to {data.conversation.assigned_to_name}</p>
        )}
      </div>
    </div>
  );
}

function PanelSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wide text-[var(--color-text-secondary)]">{title}</p>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-gray-50 px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wide text-[var(--color-text-secondary)]">{label}</p>
      <p className="font-mono-num text-xs font-bold text-[var(--color-text-primary)]">{value}</p>
    </div>
  );
}
