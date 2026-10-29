import { useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, Eye, EyeOff, ImagePlus, MessageSquareQuote, Pencil, Plus, Star, Trash2, X } from "lucide-react";
import {
  adminFaqApi,
  adminTestimonialApi,
  CONTENT_LIMITS as LIMITS,
  type AdminFaq,
  type AdminTestimonial,
} from "../../api/content";
import { uploadApi } from "../../api/upload";
import { FAQS as BUILT_IN_FAQS } from "../../data/faqs";
import { Badge, Button, EmptyState, ErrorState, Input, Modal, PageLoader, StatCard } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";

type Tab = "testimonials" | "faqs";
const TABS: { key: Tab; label: string }[] = [
  { key: "testimonials", label: "Reviews" },
  { key: "faqs", label: "FAQs" },
];

const TEXTAREA =
  "w-full rounded-[12px] border border-[#E4E9F1] bg-white px-3.5 py-2.5 text-sm text-[#0E1A33] placeholder:text-gray-400 transition-colors hover:border-[#C9D6EA] focus:border-[#0A66F0] focus:outline-none focus:ring-[3px] focus:ring-[#0A66F0]/15";

/** Keep a cached public list (landing page) honest after an admin save. */
function useRefreshPublic() {
  const queryClient = useQueryClient();
  return (admin: "admin-faqs" | "admin-testimonials", pub: "public-faqs" | "public-testimonials") => {
    void queryClient.invalidateQueries({ queryKey: [admin] });
    void queryClient.invalidateQueries({ queryKey: [pub] });
  };
}

/** Move one row up/down; every row that changes position gets a fresh, evenly spaced display_order. */
function reorder<T extends { id: string; display_order: number }>(rows: T[], id: string, dir: -1 | 1): { id: string; display_order: number }[] {
  const from = rows.findIndex((r) => r.id === id);
  const to = from + dir;
  if (from < 0 || to < 0 || to >= rows.length) return [];
  const next = [...rows];
  [next[from], next[to]] = [next[to], next[from]];
  return next.map((r, i) => ({ id: r.id, display_order: (i + 1) * 10 })).filter((n) => rows.find((r) => r.id === n.id)?.display_order !== n.display_order);
}

const nextOrder = (rows: { display_order: number }[]) => (rows.length ? Math.max(...rows.map((r) => r.display_order)) + 10 : 10);

/**
 * Admin → Reviews & FAQs: the customer reviews and the FAQ list shown on the
 * public website. Reviews are fetched live by the landing page; the landing
 * FAQ is fetched live too and falls back to the built-in default answers
 * only while none are showing.
 */
export default function AdminContentPage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "faqs" ? "faqs" : "testimonials";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Website Reviews & FAQs</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          What visitors read on the public website. A saved change shows up there within about a minute — no rebuild needed.
        </p>
      </div>

      <div className="inline-flex rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="tablist" aria-label="Content type">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setParams(t.key === "testimonials" ? {} : { tab: t.key }, { replace: true })}
            className={`rounded-[9px] px-4 py-1.5 text-sm font-medium transition ${tab === t.key ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "testimonials" ? <TestimonialsTab /> : <FaqsTab />}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Reviews                                                             */
/* ------------------------------------------------------------------ */

interface ReviewForm {
  customer_name: string;
  comment: string;
  rating: number;
  customer_image: string;
  is_featured: boolean;
}

const emptyReview: ReviewForm = { customer_name: "", comment: "", rating: 5, customer_image: "", is_featured: true };

function Stars({ value, size = "h-4 w-4" }: { value: number; size?: string }) {
  return (
    <span className="inline-flex gap-0.5" role="img" aria-label={`${value} out of 5 stars`}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Star key={i} className={`${size} ${i < value ? "fill-[#FFD21F] text-[#FFD21F]" : "fill-[#E4E9F1] text-[#E4E9F1]"}`} />
      ))}
    </span>
  );
}

function TestimonialsTab() {
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const refresh = useRefreshPublic();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["admin-testimonials"], queryFn: adminTestimonialApi.list });
  const rows = data ?? [];

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AdminTestimonial | null>(null);
  const [form, setForm] = useState<ReviewForm>(emptyReview);
  const [formError, setFormError] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyReview);
    setFormError("");
    setOpen(true);
  };
  const openEdit = (t: AdminTestimonial) => {
    setEditing(t);
    setForm({ customer_name: t.customer_name, comment: t.comment, rating: t.rating, customer_image: t.customer_image || "", is_featured: t.is_featured });
    setFormError("");
    setOpen(true);
  };

  const save = useMutation({
    mutationFn: () => {
      const body = {
        customer_name: form.customer_name.trim(),
        comment: form.comment.trim(),
        rating: form.rating,
        customer_image: form.customer_image || null,
        is_featured: form.is_featured,
      };
      return editing ? adminTestimonialApi.update(editing.id, body) : adminTestimonialApi.create({ ...body, display_order: nextOrder(rows) });
    },
    onSuccess: (res) => {
      refresh("admin-testimonials", "public-testimonials");
      pushToast({ tone: "success", title: editing ? "Review Updated" : "Review Added", message: res.message });
      setOpen(false);
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });

  const toggle = useMutation({
    mutationFn: (t: AdminTestimonial) => adminTestimonialApi.update(t.id, { is_featured: !t.is_featured }),
    onSuccess: (res, t) => {
      refresh("admin-testimonials", "public-testimonials");
      pushToast({ tone: "success", title: t.is_featured ? "Review Hidden" : "Review Showing", message: res.message });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Change The Review", message: getErrorMessage(err) }),
  });

  const move = useMutation({
    mutationFn: async (vars: { id: string; dir: -1 | 1 }) => {
      const changes = reorder(rows, vars.id, vars.dir);
      const results = await Promise.all(changes.map((c) => adminTestimonialApi.update(c.id, { display_order: c.display_order })));
      return results[results.length - 1];
    },
    onSuccess: (res) => {
      refresh("admin-testimonials", "public-testimonials");
      if (res) pushToast({ tone: "success", title: "Order Saved", message: res.message });
    },
    onError: (err) => {
      refresh("admin-testimonials", "public-testimonials");
      pushToast({ tone: "error", title: "Couldn't Save The Order", message: getErrorMessage(err) });
    },
  });

  const remove = useMutation({
    mutationFn: (t: AdminTestimonial) => adminTestimonialApi.remove(t.id),
    onSuccess: (res) => {
      refresh("admin-testimonials", "public-testimonials");
      pushToast({ tone: "success", title: "Review Deleted", message: res.message });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Delete The Review", message: getErrorMessage(err) }),
  });

  const askDelete = async (t: AdminTestimonial) => {
    const ok = await confirm({
      title: `Delete The Review From ${t.customer_name}?`,
      message: "It disappears from the website for good. To keep it but stop showing it, use Hide instead.",
      confirmLabel: "Delete Review",
      cancelLabel: "Keep It",
      tone: "danger",
    });
    if (ok) remove.mutate(t);
  };

  const onPhoto = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      setForm((f) => ({ ...f, customer_image: "" }));
      const url = await uploadApi.photo(file);
      setForm((f) => ({ ...f, customer_image: url }));
    } catch (err) {
      setFormError(getErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  if (isLoading) return <PageLoader />;
  if (isError && !data) return <ErrorState message="Couldn't load the reviews." onRetry={() => void refetch()} busy={isFetching} />;

  const showing = rows.filter((r) => r.is_featured);
  const average = showing.length ? (showing.reduce((sum, r) => sum + r.rating, 0) / showing.length).toFixed(1) : "—";
  const nameLeft = LIMITS.nameMax - form.customer_name.length;
  const formReady =
    form.customer_name.trim().length >= LIMITS.nameMin && form.comment.trim().length >= LIMITS.commentMin && form.rating >= 1 && !uploading;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="Showing On The Website" value={showing.length} hint="The Reviews section appears once at least one is showing" />
        <StatCard label="Hidden" value={rows.length - showing.length} hint="Saved but not shown to visitors" />
        <StatCard label="Average Of Showing" value={average} hint="Out of 5 — only for your reference" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-[var(--color-text-secondary)]">
          Reviews are shown on the landing page in the order below. Only reviews you add here appear — nothing is invented. Use real customers' words, with their permission.
        </p>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> Add Review
        </Button>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          icon={MessageSquareQuote}
          title="No Reviews Yet"
          description="The Reviews section stays hidden on the website until you add one."
          action={
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" /> Add The First Review
            </Button>
          }
        />
      ) : (
        <ul className="divide-y divide-[var(--color-card-border)] overflow-hidden rounded-2xl border border-[var(--color-card-border)] bg-white">
          {rows.map((t, i) => (
            <li key={t.id} className={`flex flex-wrap items-start gap-3 p-4 sm:flex-nowrap ${t.is_featured ? "" : "bg-[#FAFBFC]"}`}>
              <div className="flex shrink-0 flex-col gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="!p-1.5"
                  aria-label={`Move ${t.customer_name} up`}
                  title="Move Up"
                  disabled={i === 0 || move.isPending}
                  onClick={() => move.mutate({ id: t.id, dir: -1 })}
                >
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="!p-1.5"
                  aria-label={`Move ${t.customer_name} down`}
                  title="Move Down"
                  disabled={i === rows.length - 1 || move.isPending}
                  onClick={() => move.mutate({ id: t.id, dir: 1 })}
                >
                  <ArrowDown className="h-4 w-4" />
                </Button>
              </div>

              <div className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#E8F0FE] text-sm font-bold text-[#0A66F0]">
                {t.customer_image ? <img src={t.customer_image} alt="" className="h-full w-full object-cover" /> : initials(t.customer_name)}
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-semibold text-[#0E1A33]">{t.customer_name}</p>
                  <Stars value={t.rating} />
                  <Badge tone={t.is_featured ? "success" : "neutral"}>{t.is_featured ? "Showing" : "Hidden"}</Badge>
                </div>
                <p className="mt-1 whitespace-pre-line break-words text-sm text-[var(--color-text-secondary)]">“{t.comment}”</p>
              </div>

              <div className="flex shrink-0 gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={t.is_featured ? `Hide the review from ${t.customer_name}` : `Show the review from ${t.customer_name}`}
                  title={t.is_featured ? "Hide From Website" : "Show On Website"}
                  isLoading={toggle.isPending && toggle.variables?.id === t.id}
                  onClick={() => toggle.mutate(t)}
                >
                  {t.is_featured ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
                <Button size="sm" variant="ghost" aria-label={`Edit the review from ${t.customer_name}`} title="Edit" onClick={() => openEdit(t)}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete the review from ${t.customer_name}`}
                  title="Delete"
                  isLoading={remove.isPending && remove.variables?.id === t.id}
                  onClick={() => void askDelete(t)}
                >
                  <Trash2 className="h-4 w-4 text-[var(--color-error)]" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? "Edit Review" : "Add Review"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError("");
            save.mutate();
          }}
        >
          <Input
            label="Customer Name"
            value={form.customer_name}
            maxLength={LIMITS.nameMax}
            placeholder="e.g. Ravi S., Vijay Nagar"
            hint={`${nameLeft} characters left — add the area if the customer is fine with it`}
            onChange={(e) => setForm((f) => ({ ...f, customer_name: e.target.value }))}
            required
          />

          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Rating</p>
            <div className="flex gap-1" role="radiogroup" aria-label="Rating">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={form.rating === n}
                  aria-label={`${n} star${n === 1 ? "" : "s"}`}
                  onClick={() => setForm((f) => ({ ...f, rating: n }))}
                  className="rounded-lg p-1.5 transition hover:bg-[#FFFCF0]"
                >
                  <Star className={`h-7 w-7 ${n <= form.rating ? "fill-[#FFD21F] text-[#FFD21F]" : "fill-[#E4E9F1] text-[#E4E9F1]"}`} />
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="review-comment" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
              What They Said
            </label>
            <textarea
              id="review-comment"
              rows={4}
              maxLength={LIMITS.commentMax}
              value={form.comment}
              onChange={(e) => setForm((f) => ({ ...f, comment: e.target.value }))}
              placeholder="The captain was on time and the car looked brand new."
              className={TEXTAREA}
              required
            />
            <p className="mt-1 text-right text-xs text-[var(--color-text-secondary)]">
              {form.comment.length}/{LIMITS.commentMax}
            </p>
          </div>

          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Photo (Optional)</p>
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#E8F0FE] text-sm font-bold text-[#0A66F0]">
                {form.customer_image ? <img src={form.customer_image} alt="Customer" className="h-full w-full object-cover" /> : initials(form.customer_name)}
              </div>
              <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; void onPhoto(f); }} />
              <Button type="button" variant="outline" size="sm" isLoading={uploading} onClick={() => fileRef.current?.click()}>
                <ImagePlus className="h-4 w-4" /> {form.customer_image ? "Change Photo" : "Upload Photo"}
              </Button>
              {form.customer_image && (
                <Button type="button" variant="ghost" size="sm" aria-label="Remove photo" onClick={() => setForm((f) => ({ ...f, customer_image: "" }))}>
                  <X className="h-4 w-4" /> Remove
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Without a photo the website shows the customer's initials.</p>
          </div>

          <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--color-text-primary)]">
            <input type="checkbox" className="h-4 w-4 accent-[#0E1A33]" checked={form.is_featured} onChange={(e) => setForm((f) => ({ ...f, is_featured: e.target.checked }))} />
            Show This Review On The Website
          </label>

          {formError && (
            <p role="alert" className="text-sm text-[var(--color-error)]">
              {formError}
            </p>
          )}
          <Button type="submit" className="w-full" isLoading={save.isPending} disabled={!formReady}>
            {editing ? "Save Changes" : "Add Review"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

/* ------------------------------------------------------------------ */
/* FAQs                                                                */
/* ------------------------------------------------------------------ */

interface FaqForm {
  question: string;
  answer: string;
  is_active: boolean;
}

const emptyFaq: FaqForm = { question: "", answer: "", is_active: true };

function FaqsTab() {
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const refresh = useRefreshPublic();
  const { data, isLoading, isError, isFetching, refetch } = useQuery({ queryKey: ["admin-faqs"], queryFn: adminFaqApi.list });
  const rows = data ?? [];

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AdminFaq | null>(null);
  const [form, setForm] = useState<FaqForm>(emptyFaq);
  const [formError, setFormError] = useState("");

  const openCreate = () => {
    setEditing(null);
    setForm(emptyFaq);
    setFormError("");
    setOpen(true);
  };
  const openEdit = (f: AdminFaq) => {
    setEditing(f);
    setForm({ question: f.question, answer: f.answer, is_active: f.is_active });
    setFormError("");
    setOpen(true);
  };

  const save = useMutation({
    mutationFn: () => {
      const body = { question: form.question.trim(), answer: form.answer.trim(), is_active: form.is_active };
      return editing ? adminFaqApi.update(editing.id, body) : adminFaqApi.create({ ...body, display_order: nextOrder(rows) });
    },
    onSuccess: (res) => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "success", title: editing ? "FAQ Updated" : "FAQ Added", message: res.message });
      setOpen(false);
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });

  const toggle = useMutation({
    mutationFn: (f: AdminFaq) => adminFaqApi.update(f.id, { is_active: !f.is_active }),
    onSuccess: (res, f) => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "success", title: f.is_active ? "FAQ Hidden" : "FAQ Showing", message: res.message });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Change The FAQ", message: getErrorMessage(err) }),
  });

  const move = useMutation({
    mutationFn: async (vars: { id: string; dir: -1 | 1 }) => {
      const changes = reorder(rows, vars.id, vars.dir);
      const results = await Promise.all(changes.map((c) => adminFaqApi.update(c.id, { display_order: c.display_order })));
      return results[results.length - 1];
    },
    onSuccess: (res) => {
      refresh("admin-faqs", "public-faqs");
      if (res) pushToast({ tone: "success", title: "Order Saved", message: res.message });
    },
    onError: (err) => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "error", title: "Couldn't Save The Order", message: getErrorMessage(err) });
    },
  });

  const remove = useMutation({
    mutationFn: (f: AdminFaq) => adminFaqApi.remove(f.id),
    onSuccess: (res) => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "success", title: "FAQ Deleted", message: res.message });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Delete The FAQ", message: getErrorMessage(err) }),
  });

  // Seeds the list with the answers that ship inside the website, so the
  // admin starts from the current text instead of retyping it.
  const importBuiltIn = useMutation({
    mutationFn: async () => {
      let last: Awaited<ReturnType<typeof adminFaqApi.create>> | undefined;
      for (const [i, f] of BUILT_IN_FAQS.entries()) {
        last = await adminFaqApi.create({ question: f.question, answer: f.answer, display_order: (i + 1) * 10, is_active: true });
      }
      return last;
    },
    onSuccess: () => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "success", title: "Built-In FAQs Imported", message: `${BUILT_IN_FAQS.length} FAQs added — edit, hide or reorder them below.` });
    },
    onError: (err) => {
      refresh("admin-faqs", "public-faqs");
      pushToast({ tone: "error", title: "Couldn't Import The FAQs", message: getErrorMessage(err) });
    },
  });

  const askDelete = async (f: AdminFaq) => {
    const ok = await confirm({
      title: "Delete This FAQ?",
      message: `"${f.question}" disappears from the website for good. To keep it but stop showing it, use Hide instead.`,
      confirmLabel: "Delete FAQ",
      cancelLabel: "Keep It",
      tone: "danger",
    });
    if (ok) remove.mutate(f);
  };

  if (isLoading) return <PageLoader />;
  if (isError && !data) return <ErrorState message="Couldn't load the FAQs." onRetry={() => void refetch()} busy={isFetching} />;

  const showing = rows.filter((r) => r.is_active);
  const formReady = form.question.trim().length >= LIMITS.questionMin && form.answer.trim().length >= LIMITS.answerMin;

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label="Showing On The Website" value={showing.length} hint={showing.length ? undefined : "None showing — visitors see the built-in answers"} />
        <StatCard label="Hidden" value={rows.length - showing.length} hint="Saved but not shown to visitors" />
        <StatCard label="Total" value={rows.length} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-[var(--color-text-secondary)]">
          The landing page FAQ shows the questions below, in this order. While none are showing it falls back to the answers that ship with the website, so the
          section is never empty. The Services page keeps its own short list.
        </p>
        <div className="flex flex-wrap gap-2">
          {rows.length === 0 && (
            <Button variant="outline" isLoading={importBuiltIn.isPending} onClick={() => importBuiltIn.mutate()}>
              Import Built-In FAQs
            </Button>
          )}
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" /> Add FAQ
          </Button>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="No FAQs Saved Yet"
          description="Import the built-in answers to start from the current text, or add your own."
          action={
            <Button isLoading={importBuiltIn.isPending} onClick={() => importBuiltIn.mutate()}>
              Import Built-In FAQs
            </Button>
          }
        />
      ) : (
        <ul className="divide-y divide-[var(--color-card-border)] overflow-hidden rounded-2xl border border-[var(--color-card-border)] bg-white">
          {rows.map((f, i) => (
            <li key={f.id} className={`flex flex-wrap items-start gap-3 p-4 sm:flex-nowrap ${f.is_active ? "" : "bg-[#FAFBFC]"}`}>
              <div className="flex shrink-0 flex-col gap-1">
                <Button size="sm" variant="ghost" className="!p-1.5" aria-label={`Move "${f.question}" up`} title="Move Up" disabled={i === 0 || move.isPending} onClick={() => move.mutate({ id: f.id, dir: -1 })}>
                  <ArrowUp className="h-4 w-4" />
                </Button>
                <Button size="sm" variant="ghost" className="!p-1.5" aria-label={`Move "${f.question}" down`} title="Move Down" disabled={i === rows.length - 1 || move.isPending} onClick={() => move.mutate({ id: f.id, dir: 1 })}>
                  <ArrowDown className="h-4 w-4" />
                </Button>
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-semibold text-[#0E1A33]">{f.question}</p>
                  <Badge tone={f.is_active ? "success" : "neutral"}>{f.is_active ? "Showing" : "Hidden"}</Badge>
                </div>
                <p className="mt-1 line-clamp-3 whitespace-pre-line break-words text-sm text-[var(--color-text-secondary)]">{f.answer}</p>
              </div>

              <div className="flex shrink-0 gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={f.is_active ? `Hide "${f.question}"` : `Show "${f.question}"`}
                  title={f.is_active ? "Hide From Website" : "Show On Website"}
                  isLoading={toggle.isPending && toggle.variables?.id === f.id}
                  onClick={() => toggle.mutate(f)}
                >
                  {f.is_active ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
                <Button size="sm" variant="ghost" aria-label={`Edit "${f.question}"`} title="Edit" onClick={() => openEdit(f)}>
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button size="sm" variant="ghost" aria-label={`Delete "${f.question}"`} title="Delete" isLoading={remove.isPending && remove.variables?.id === f.id} onClick={() => void askDelete(f)}>
                  <Trash2 className="h-4 w-4 text-[var(--color-error)]" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? "Edit FAQ" : "Add FAQ"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError("");
            save.mutate();
          }}
        >
          <Input
            label="Question"
            value={form.question}
            maxLength={LIMITS.questionMax}
            placeholder="How does the monthly pass work?"
            hint={`${LIMITS.questionMax - form.question.length} characters left`}
            onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))}
            required
          />
          <FieldBlock id="faq-answer" label="Answer" counter={`${form.answer.length}/${LIMITS.answerMax}`}>
            <textarea
              id="faq-answer"
              rows={6}
              maxLength={LIMITS.answerMax}
              value={form.answer}
              onChange={(e) => setForm((f) => ({ ...f, answer: e.target.value }))}
              placeholder="Keep it short, plain and true to how bookings, plans and payments really work."
              className={TEXTAREA}
              required
            />
          </FieldBlock>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--color-text-primary)]">
            <input type="checkbox" className="h-4 w-4 accent-[#0E1A33]" checked={form.is_active} onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))} />
            Show This FAQ On The Website
          </label>
          {formError && (
            <p role="alert" className="text-sm text-[var(--color-error)]">
              {formError}
            </p>
          )}
          <Button type="submit" className="w-full" isLoading={save.isPending} disabled={!formReady}>
            {editing ? "Save Changes" : "Add FAQ"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}

function FieldBlock({ id, label, counter, children }: { id: string; label: string; counter: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
        {label}
      </label>
      {children}
      <p className="mt-1 text-right text-xs text-[var(--color-text-secondary)]">{counter}</p>
    </div>
  );
}
