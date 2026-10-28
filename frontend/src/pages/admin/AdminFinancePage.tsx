import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, Wallet, Ban } from "lucide-react";
import { financeApi, type FinanceEntry, type FinanceEntryInput, type FinanceType } from "../../api/finance";
import { Badge, Button, Input, Modal, Panel, Select, StatCard } from "../../components/ui";
import { Pager, SearchBox, useDebouncedValue } from "../../components/shared/ListControls";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { format as formatDate, todayIST } from "../../lib/date";

const rupees = (paise: number) => `${paise < 0 ? "-" : ""}₹${(Math.abs(paise) / 100).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: Math.abs(paise) % 100 ? 2 : 0 })}`;

type Preset = "today" | "7d" | "month" | "last" | "year" | "custom";
const PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7d", label: "7 Days" },
  { key: "month", label: "This Month" },
  { key: "last", label: "Last Month" },
  { key: "year", label: "This Year" },
  { key: "custom", label: "Custom" },
];

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function rangeOf(preset: Preset, custom: { from: string; to: string }): { from: string; to: string } {
  const today = todayIST();
  const [y, m] = today.split("-").map(Number);
  const pad = (n: number) => String(n).padStart(2, "0");
  switch (preset) {
    case "today":
      return { from: today, to: today };
    case "7d":
      return { from: shift(today, -6), to: today };
    case "month":
      return { from: `${y}-${pad(m)}-01`, to: today };
    case "last": {
      const first = `${y}-${pad(m)}-01`;
      const lastDay = shift(first, -1);
      return { from: `${lastDay.slice(0, 8)}01`, to: lastDay };
    }
    case "year":
      return { from: `${y}-01-01`, to: today };
    default:
      return custom;
  }
}

const SUGGESTED_FALLBACK = ["Salary", "Marketing", "Rent", "Product Purchase", "Product Sale", "Utilities", "Travel", "Other"];
const TEXTAREA =
  "w-full rounded-[12px] border border-[var(--color-card-border)] bg-white px-3.5 py-2.5 text-sm text-[#0E1A33] placeholder:text-gray-400 transition-colors focus:border-black focus:outline-none";

interface Form {
  title: string;
  type: FinanceType;
  amount: string;
  date: string;
  category: string;
  description: string;
  doneBy: string;
  reference: string;
}

const emptyForm = (doneBy: string): Form => ({ title: "", type: "debit", amount: "", date: todayIST(), category: "", description: "", doneBy, reference: "" });
const formOf = (e: FinanceEntry): Form => ({
  title: e.title,
  type: e.type,
  amount: String(e.amount_rupees),
  date: e.entry_date,
  category: e.category || "",
  description: e.description,
  doneBy: e.done_by,
  reference: e.reference || "",
});

/** Rupees typed by the admin → whole paise, or null when not a valid amount. */
function toPaise(text: string): number | null {
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(text.trim())) return null;
  const paise = Math.round(Number(text) * 100);
  return paise > 0 && paise <= 1_00_00_000 * 100 ? paise : null;
}

const SIGN = { credit: "+", debit: "-" } as const;

/**
 * Admin → Expenses & Sales: the simple credit/debit book for money that is
 * NOT a booking (salary, marketing, rent, product purchases and sales).
 * The bookings dashboard is untouched; this page only READS the bookings'
 * received money so the account balance can show everything in one place.
 */
export default function AdminFinancePage() {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const { user } = useAuth();
  const nameKey = `blussit_finance_done_by_${user?.id ?? "admin"}`;
  const lastName = () => {
    try {
      return localStorage.getItem(nameKey) || "";
    } catch {
      return "";
    }
  };

  // Period for the StatCards (and the breakdown).
  const [preset, setPreset] = useState<Preset>("month");
  const [custom, setCustom] = useState({ from: todayIST(), to: todayIST() });
  const range = useMemo(() => rangeOf(preset, custom), [preset, custom]);
  const rangeOk = !!range.from && !!range.to && range.from <= range.to;

  // Table filters (their own date range: empty = all time).
  const [typeFilter, setTypeFilter] = useState<"" | FinanceType>("");
  const [category, setCategory] = useState("");
  const [doneBy, setDoneBy] = useState("");
  const [search, setSearch] = useState("");
  const [tFrom, setTFrom] = useState("");
  const [tTo, setTTo] = useState("");
  const [showVoided, setShowVoided] = useState(false);
  const [page, setPage] = useState(1);
  const q = useDebouncedValue(search, 350);
  const doneByQ = useDebouncedValue(doneBy, 350);
  const filters = { type: typeFilter, category, from: tFrom, to: tTo, q, done_by: doneByQ, include_voided: showVoided };
  useEffect(() => setPage(1), [typeFilter, category, tFrom, tTo, q, doneByQ, showVoided]);

  const summary = useQuery({
    queryKey: ["finance-summary", range.from, range.to],
    queryFn: () => financeApi.summary(range.from, range.to),
    enabled: rangeOk,
    placeholderData: keepPreviousData,
  });
  const list = useQuery({
    queryKey: ["finance-entries", filters, page],
    queryFn: () => financeApi.list({ ...filters, page, page_size: 20 }),
    placeholderData: keepPreviousData,
  });
  const cats = useQuery({ queryKey: ["finance-categories"], queryFn: financeApi.categories, staleTime: 60_000 });
  const categoryOptions = cats.data?.all ?? SUGGESTED_FALLBACK;
  const s = summary.data;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["finance-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["finance-entries"] });
    void queryClient.invalidateQueries({ queryKey: ["finance-categories"] });
  };

  // ----- add / edit -----
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<FinanceEntry | null>(null);
  const [form, setForm] = useState<Form>(() => emptyForm(""));
  const [formError, setFormError] = useState("");
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));
  const openAdd = () => {
    setEditing(null);
    setForm(emptyForm(lastName()));
    setFormError("");
    setOpen(true);
  };
  const openEdit = (e: FinanceEntry) => {
    setEditing(e);
    setForm(formOf(e));
    setFormError("");
    setOpen(true);
  };
  const save = useMutation({
    mutationFn: (payload: FinanceEntryInput) => (editing ? financeApi.update(editing.id, payload) : financeApi.create(payload)),
    onSuccess: (_d, payload) => {
      try {
        localStorage.setItem(nameKey, payload.done_by);
      } catch {
        /* private mode — the name just isn't remembered */
      }
      pushToast({ tone: "success", title: editing ? "Entry Updated" : "Entry Added", message: "The totals are up to date." });
      setOpen(false);
      refresh();
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });
  const submit = () => {
    setFormError("");
    const paise = toPaise(form.amount);
    if (!form.title.trim()) return setFormError("Enter a name for this entry.");
    if (paise == null) return setFormError("Enter an amount in rupees, like 1500 or 1500.50 (up to ₹1 crore).");
    if (!form.doneBy.trim()) return setFormError("Enter who did this.");
    save.mutate({
      title: form.title.trim(),
      type: form.type,
      amount_paise: paise,
      entry_date: form.date || todayIST(),
      category: form.category.trim() || null,
      description: form.description.trim(),
      done_by: form.doneBy.trim(),
      reference: form.reference.trim() || null,
    });
  };

  // ----- void -----
  const [voiding, setVoiding] = useState<FinanceEntry | null>(null);
  const [reason, setReason] = useState("");
  const [voidError, setVoidError] = useState("");
  const voidIt = useMutation({
    mutationFn: () => financeApi.void(voiding!.id, reason.trim()),
    onSuccess: () => {
      pushToast({ tone: "success", title: "Entry Voided", message: "It no longer counts in any total." });
      setVoiding(null);
      setReason("");
      refresh();
    },
    onError: (err) => setVoidError(getErrorMessage(err)),
  });

  // ----- export -----
  const exporting = useMutation({
    mutationFn: () => financeApi.exportCsv(filters),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `blussit-finance-${todayIST()}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Export", message: getErrorMessage(err) }),
  });

  const entries = list.data?.data ?? [];
  const maxCat = Math.max(1, ...(s?.by_category ?? []).map((c) => c.credit_paise + c.debit_paise));
  const money = (paise?: number) => (paise == null ? "—" : rupees(paise));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Expenses &amp; Sales</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Salary, marketing, product sales and other credits and debits — kept apart from bookings.</p>
        </div>
        <Button onClick={openAdd}>
          <Plus className="h-4 w-4" /> Add Entry
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="inline-flex max-w-full flex-wrap rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="tablist" aria-label="Period">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              role="tab"
              aria-selected={preset === p.key}
              onClick={() => setPreset(p.key)}
              className={`rounded-[9px] px-3 py-1.5 text-sm font-medium transition ${preset === p.key ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="flex gap-2">
            <Input label="From" type="date" value={custom.from} max={todayIST()} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))} />
            <Input label="To" type="date" value={custom.to} max={todayIST()} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))} />
          </div>
        )}
        {!rangeOk && <p className="text-sm text-[var(--color-error)]">Pick a start date that is not after the end date.</p>}
      </div>

      <StatCard
        label="Total In Blussit Account"
        icon={Wallet}
        value={money(s?.total_balance_paise)}
        hint="All time: bookings earning + credits − debits"
        className="border-[#0E1A33]"
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Bookings Earning" value={money(s?.bookings_earning_paise)} hint="Money received, net of paybacks" />
        <StatCard label="Other Credits" tone="success" value={money(s?.credits_paise)} hint={s ? `${s.credit_count} Entr${s.credit_count === 1 ? "y" : "ies"}` : undefined} />
        <StatCard label="Debits" tone="error" value={money(s?.debits_paise)} hint={s ? `${s.debit_count} Entr${s.debit_count === 1 ? "y" : "ies"}` : undefined} />
        <StatCard label="Net For Period" value={money(s?.net_period_paise)} tone={s && s.net_period_paise < 0 ? "error" : "default"} hint="Bookings + credits − debits" />
      </div>
      {s && s.plan_sales_paise > 0 && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          Plan sales received in this period: {rupees(s.plan_sales_paise)} — shown for information, not counted above.
        </p>
      )}

      <Panel title="By Category" description="Credits and debits in the chosen period">
        {!s || s.by_category.length === 0 ? (
          <p className="text-sm text-[var(--color-text-secondary)]">No entries in this period.</p>
        ) : (
          <ul className="space-y-3">
            {s.by_category.map((c) => (
              <li key={c.category}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                  <span className="font-medium text-[#0E1A33]">{c.category}</span>
                  <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">
                    {c.credit_paise > 0 && <>Credit {rupees(c.credit_paise)}</>}
                    {c.credit_paise > 0 && c.debit_paise > 0 && " · "}
                    {c.debit_paise > 0 && <>Debit {rupees(c.debit_paise)}</>}
                  </span>
                </div>
                <div className="mt-1 flex h-2 overflow-hidden rounded-full bg-[#FFFCF0]" aria-hidden>
                  <div className="h-full bg-[#E0B83A]" style={{ width: `${(c.credit_paise / maxCat) * 100}%` }} />
                  <div className="h-full bg-[#0E1A33]" style={{ width: `${(c.debit_paise / maxCat) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 flex gap-4 text-xs text-[var(--color-text-secondary)]">
          <span className="flex items-center gap-1.5"><i className="inline-block h-2 w-2 rounded-full bg-[#E0B83A]" /> Credit</span>
          <span className="flex items-center gap-1.5"><i className="inline-block h-2 w-2 rounded-full bg-[#0E1A33]" /> Debit</span>
        </p>
      </Panel>

      <Panel
        title="Entries"
        actions={
          <>
            <label className="flex cursor-pointer items-center gap-2 text-sm text-[#5F6878]">
              <input type="checkbox" className="h-4 w-4 accent-[#0E1A33]" checked={showVoided} onChange={(e) => setShowVoided(e.target.checked)} />
              Show Voided
            </label>
            <Button size="sm" variant="outline" isLoading={exporting.isPending} onClick={() => exporting.mutate()}>
              <Download className="h-3.5 w-3.5" /> Export CSV
            </Button>
          </>
        }
      >
        <div className="mb-4 grid grid-cols-2 gap-2 lg:grid-cols-6">
          <SearchBox value={search} onChange={setSearch} placeholder="Search Entries" className="col-span-2" />
          <Select compact aria-label="Type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as "" | FinanceType)}>
            <option value="">All Types</option>
            <option value="credit">Credit</option>
            <option value="debit">Debit</option>
          </Select>
          <Select compact aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All Categories</option>
            {categoryOptions.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <input
            aria-label="Done By"
            value={doneBy}
            onChange={(e) => setDoneBy(e.target.value)}
            placeholder="Done By"
            className="h-10 min-w-0 rounded-xl border border-[var(--color-card-border)] bg-white px-3 text-sm focus:border-black focus:outline-none"
          />
          <div />
          <Input label="From" type="date" value={tFrom} max={todayIST()} onChange={(e) => setTFrom(e.target.value)} />
          <Input label="To" type="date" value={tTo} max={todayIST()} onChange={(e) => setTTo(e.target.value)} />
          {(tFrom || tTo) && (
            <button type="button" className="self-end pb-2 text-left text-sm text-[#5F6878] underline" onClick={() => { setTFrom(""); setTTo(""); }}>
              Clear Dates
            </button>
          )}
        </div>

        {list.isLoading ? (
          <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">Loading…</p>
        ) : list.isError ? (
          <p className="py-8 text-center text-sm text-[var(--color-error)]">{getErrorMessage(list.error)}</p>
        ) : entries.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--color-text-secondary)]">No entries yet. Use Add Entry to record the first one.</p>
        ) : (
          <>
            {/* Desktop table */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-card-border)] text-[13px] text-[#5F6878]">
                    {["Date", "Name", "Category", "Done By", "Amount", ""].map((h) => (
                      <th key={h} className={`px-3 py-2 font-medium ${h === "Amount" ? "text-right" : ""}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr key={e.id} className={`border-b border-[var(--color-card-border)] align-top last:border-0 hover:bg-[#FFFCF0] ${e.is_deleted ? "bg-gray-50 text-gray-400" : ""}`}>
                      <td className="whitespace-nowrap px-3 py-3">{formatDate(e.entry_date)}</td>
                      <td className="px-3 py-3">
                        <p className={`font-semibold ${e.is_deleted ? "line-through" : "text-[#0E1A33]"}`}>{e.title}</p>
                        {e.description && <p className="max-w-[340px] text-xs text-[#5F6878]">{e.description}</p>}
                        {e.reference && <p className="text-xs text-[#5F6878]">Ref: {e.reference}</p>}
                        {e.is_deleted && <p className="text-xs text-[var(--color-error)]">Voided: {e.delete_reason}</p>}
                      </td>
                      <td className="px-3 py-3">{e.category || "—"}</td>
                      <td className="px-3 py-3">{e.done_by}</td>
                      <td className={`font-mono-num whitespace-nowrap px-3 py-3 text-right font-semibold ${e.is_deleted ? "line-through" : e.type === "credit" ? "text-[var(--color-success)]" : "text-[#0E1A33]"}`}>
                        {SIGN[e.type]} {rupees(e.amount_paise)}
                      </td>
                      <td className="px-3 py-3">
                        {e.is_deleted ? (
                          <Badge tone="neutral">Voided</Badge>
                        ) : (
                          <div className="flex justify-end gap-2">
                            <Button size="sm" variant="ghost" aria-label={`Edit ${e.title}`} title="Edit" onClick={() => openEdit(e)}><Pencil className="h-3.5 w-3.5" /></Button>
                            <Button size="sm" variant="outline" onClick={() => { setVoiding(e); setReason(""); setVoidError(""); }}><Ban className="h-3.5 w-3.5" /> Void</Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Mobile cards */}
            <ul className="space-y-3 md:hidden">
              {entries.map((e) => (
                <li key={e.id} className={`rounded-xl border border-[var(--color-card-border)] p-3 ${e.is_deleted ? "bg-gray-50 text-gray-400" : ""}`}>
                  <div className="flex items-start justify-between gap-3">
                    <p className={`min-w-0 break-words font-semibold ${e.is_deleted ? "line-through" : "text-[#0E1A33]"}`}>{e.title}</p>
                    <p className={`font-mono-num shrink-0 font-semibold ${e.is_deleted ? "line-through" : e.type === "credit" ? "text-[var(--color-success)]" : "text-[#0E1A33]"}`}>
                      {SIGN[e.type]} {rupees(e.amount_paise)}
                    </p>
                  </div>
                  <p className="mt-1 text-xs text-[#5F6878]">
                    {formatDate(e.entry_date)} · {e.category || "No Category"} · {e.done_by}
                  </p>
                  {e.description && <p className="mt-1 break-words text-xs text-[#5F6878]">{e.description}</p>}
                  {e.reference && <p className="text-xs text-[#5F6878]">Ref: {e.reference}</p>}
                  {e.is_deleted ? (
                    <p className="mt-2 text-xs text-[var(--color-error)]"><Badge tone="neutral">Voided</Badge> {e.delete_reason}</p>
                  ) : (
                    <div className="mt-2 flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => openEdit(e)}><Pencil className="h-3.5 w-3.5" /> Edit</Button>
                      <Button size="sm" variant="outline" onClick={() => { setVoiding(e); setReason(""); setVoidError(""); }}><Ban className="h-3.5 w-3.5" /> Void</Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {list.data?.meta && (
          <div className="mt-4">
            <Pager page={page} totalPages={list.data.meta.total_pages} total={list.data.meta.total} onPage={setPage} busy={list.isFetching} />
          </div>
        )}
      </Panel>

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? "Edit Entry" : "Add Entry"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="inline-flex rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="group" aria-label="Credit or debit">
            {(["credit", "debit"] as const).map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={form.type === t}
                onClick={() => set("type", t)}
                className={`rounded-[9px] px-5 py-1.5 text-sm font-medium transition ${form.type === t ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
              >
                {t === "credit" ? "Credit (Money In)" : "Debit (Money Out)"}
              </button>
            ))}
          </div>
          <Input label="Name" value={form.title} maxLength={120} placeholder="e.g. October Salary, Wax Kits Sold" onChange={(e) => set("title", e.target.value)} required />
          <div className="grid grid-cols-2 gap-3">
            <Input label="Amount (₹)" inputMode="decimal" value={form.amount} placeholder="1500" onChange={(e) => set("amount", e.target.value.replace(/[^\d.]/g, ""))} required />
            <Input label="Date" type="date" value={form.date} max={shiftIST(1)} onChange={(e) => set("date", e.target.value)} required />
          </div>
          <div>
            <Input label="Category (Optional)" list="finance-categories" value={form.category} maxLength={50} placeholder="Pick or type your own" onChange={(e) => set("category", e.target.value)} />
            <datalist id="finance-categories">
              {categoryOptions.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </div>
          <div>
            <label htmlFor="finance-desc" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">Short Description</label>
            <textarea id="finance-desc" rows={2} maxLength={500} value={form.description} onChange={(e) => set("description", e.target.value)} className={TEXTAREA} placeholder="What was this for?" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Done By" value={form.doneBy} maxLength={100} placeholder="Person's name" onChange={(e) => set("doneBy", e.target.value)} required />
            <Input label="Reference (Optional)" value={form.reference} maxLength={80} placeholder="Invoice or UPI ref" onChange={(e) => set("reference", e.target.value)} />
          </div>
          {formError && <p role="alert" className="text-sm text-[var(--color-error)]">{formError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" isLoading={save.isPending}>{editing ? "Save Changes" : "Add Entry"}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={!!voiding} onClose={() => setVoiding(null)} title="Void This Entry?" maxWidth="max-w-md">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setVoidError("");
            if (reason.trim().length < 3) return setVoidError("Write a short reason (at least 3 letters).");
            voidIt.mutate();
          }}
        >
          <p className="text-sm text-[#5F6878]">
            {voiding?.title} · {voiding ? rupees(voiding.amount_paise) : ""}. It stays in the records but stops counting in every total.
          </p>
          <Input label="Reason" value={reason} maxLength={200} placeholder="e.g. Entered twice" onChange={(e) => setReason(e.target.value)} required />
          {voidError && <p role="alert" className="text-sm text-[var(--color-error)]">{voidError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setVoiding(null)}>Keep It</Button>
            <Button type="submit" variant="danger" isLoading={voidIt.isPending}>Void Entry</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

function shiftIST(days: number): string {
  return shift(todayIST(), days);
}
