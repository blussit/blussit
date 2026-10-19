import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarOff, Pencil, Plus, Power } from "lucide-react";
import { adminHolidayApi, type AdminHoliday, type HolidayInput, type HolidaySaveResult } from "../../api/holidays";
import { adminServiceCenterApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal } from "../../components/ui";
import { HolidayCard } from "../../components/public/HolidayPopup";
import { Pager } from "../../components/shared/ListControls";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime, todayIST } from "../../lib/date";

type Tab = "upcoming" | "active" | "past";
const TABS: { key: Tab; label: string }[] = [
  { key: "upcoming", label: "Upcoming" },
  { key: "active", label: "Active" },
  { key: "past", label: "Past" },
];

interface Form {
  title: string;
  message: string;
  scope: "all" | "centers";
  centerIds: string[];
  startDate: string;
  startTime: string;
  endDate: string;
  endTime: string;
  noticeDays: string;
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00+05:30`);
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}

function emptyForm(): Form {
  const start = addDays(todayIST(), 1);
  return { title: "", message: "", scope: "all", centerIds: [], startDate: start, startTime: "", endDate: start, endTime: "", noticeDays: "2" };
}

function formOf(h: AdminHoliday): Form {
  return {
    title: h.title || "",
    message: h.message,
    scope: h.scope,
    centerIds: h.service_center_ids,
    startDate: h.start_date,
    startTime: h.start_time || "",
    endDate: h.end_date,
    endTime: h.end_time || "",
    noticeDays: String(h.notice_days_before ?? 2),
  };
}

function payloadOf(f: Form): HolidayInput {
  return {
    title: f.title.trim() || null,
    message: f.message.trim(),
    scope: f.scope,
    service_center_ids: f.scope === "centers" ? f.centerIds : [],
    start_date: f.startDate,
    start_time: f.startTime || null,
    end_date: f.endDate,
    end_time: f.endTime || null,
    notice_days_before: Math.max(0, Math.min(30, Number(f.noticeDays) || 0)),
  };
}

const TEXTAREA =
  "w-full rounded-[12px] border border-[#E4E9F1] bg-white px-3.5 py-2.5 text-sm text-[#0E1A33] placeholder:text-gray-400 transition-colors focus:border-[#0A66F0] focus:outline-none focus:ring-[3px] focus:ring-[#0A66F0]/15";

/**
 * Admin → Holidays: declare a break for every center or a few, for a day
 * or a stretch. While it runs nobody can book those times; the website
 * shows the popup from "Show Popup From" days before. Existing bookings
 * inside it are NOT cancelled — the dialog lists them so they can be
 * handled.
 */
export default function AdminHolidaysPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const [tab, setTab] = useState<Tab>("upcoming");
  const [page, setPage] = useState(1);
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["admin-holidays", tab, page],
    queryFn: () => adminHolidayApi.list({ status: tab, page, page_size: 20 }),
    placeholderData: keepPreviousData,
  });
  const { data: centers } = useQuery({
    queryKey: ["admin-centers-for-holidays"],
    queryFn: () => adminServiceCenterApi.list({ page_size: 100, active_only: true }),
    staleTime: 5 * 60_000,
  });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AdminHoliday | null>(null);
  const [form, setForm] = useState<Form>(emptyForm);
  const [formError, setFormError] = useState("");

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm());
    setFormError("");
    setOpen(true);
  };
  const openEdit = (h: AdminHoliday) => {
    setEditing(h);
    setForm(formOf(h));
    setFormError("");
    setOpen(true);
  };
  const close = () => setOpen(false);

  // Live preview: the server's own validation, labels, popup and the
  // existing bookings inside the dates — nothing saved.
  const payload = useMemo(() => payloadOf(form), [form]);
  const ready = !!payload.message && !!payload.start_date && !!payload.end_date && (payload.scope === "all" || payload.service_center_ids.length > 0);
  const [debounced, setDebounced] = useState<HolidayInput | null>(null);
  useEffect(() => {
    if (!open || !ready) {
      setDebounced(null);
      return;
    }
    const t = window.setTimeout(() => setDebounced(payload), 350);
    return () => window.clearTimeout(t);
  }, [open, ready, payload]);
  const preview = useQuery({
    queryKey: ["admin-holiday-preview", editing?.id || "new", debounced],
    queryFn: () => adminHolidayApi.preview(debounced as HolidayInput, editing?.id),
    enabled: !!debounced,
    retry: false,
    placeholderData: keepPreviousData,
  });

  const afterSave = (result: HolidaySaveResult, verb: string) => {
    queryClient.invalidateQueries({ queryKey: ["admin-holidays"] });
    queryClient.invalidateQueries({ queryKey: ["holidays"] });
    queryClient.invalidateQueries({ queryKey: ["available-slots"] });
    const n = result.affected_bookings.count;
    pushToast({
      tone: n ? "warning" : "success",
      title: `Holiday ${verb}`,
      message: n ? `${n} existing booking${n === 1 ? "" : "s"} fall inside it — they were not cancelled.` : `Booking reopens ${result.holiday.resume_label}.`,
    });
    close();
  };
  const save = useMutation({
    mutationFn: () => (editing ? adminHolidayApi.update(editing.id, payload) : adminHolidayApi.create(payload)),
    onSuccess: (result) => afterSave(result, editing ? "Updated" : "Saved"),
    onError: (err) => setFormError(getErrorMessage(err)),
  });
  const end = useMutation({
    mutationFn: (h: AdminHoliday) => adminHolidayApi.end(h.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-holidays"] });
      queryClient.invalidateQueries({ queryKey: ["holidays"] });
      queryClient.invalidateQueries({ queryKey: ["available-slots"] });
      pushToast({ tone: "success", title: "Holiday Ended", message: "Booking is open again." });
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't End The Holiday", message: getErrorMessage(err) }),
  });

  const askEnd = async (h: AdminHoliday) => {
    const running = h.status === "active";
    const ok = await confirm({
      title: running ? "End This Holiday Now?" : "Cancel This Holiday?",
      message: running
        ? `Booking opens again right away (it was closed ${h.dates_label}).`
        : `${h.dates_label} will be open for booking again and the website popup stops.`,
      confirmLabel: running ? "End Holiday Now" : "Cancel Holiday",
      cancelLabel: "Keep It",
      tone: "danger",
    });
    if (ok) end.mutate(h);
  };

  const centerOptions = centers?.data ?? [];
  const toggleCenter = (id: string) =>
    setForm((f) => ({ ...f, centerIds: f.centerIds.includes(id) ? f.centerIds.filter((c) => c !== id) : [...f.centerIds, id] }));
  const previewData = debounced ? preview.data : undefined;
  const previewError = debounced && preview.isError ? getErrorMessage(preview.error) : "";
  const affected = previewData?.affected_bookings;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Holidays</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Close booking for a day or a few — all centers or selected ones. Customers see a friendly popup.</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> Add Holiday
        </Button>
      </div>

      <div className="inline-flex rounded-[12px] border border-[var(--color-card-border)] bg-white p-1" role="tablist" aria-label="Holiday status">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => {
              setTab(t.key);
              setPage(1);
            }}
            className={`rounded-[9px] px-4 py-1.5 text-sm font-medium transition ${tab === t.key ? "bg-[#FFF4CD] text-[#0E1A33] ring-1 ring-[#0E1A33]" : "text-[#5F6878] hover:text-[#0E1A33]"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <DataTable<AdminHoliday>
        isLoading={isLoading}
        data={data?.data || []}
        error={error}
        onRetry={() => void refetch()}
        emptyTitle={tab === "upcoming" ? "No Upcoming Holidays" : tab === "active" ? "No Holiday Running Now" : "No Past Holidays"}
        columns={[
          {
            header: "Dates",
            accessor: (h) => (
              <div className="min-w-[150px]">
                <p className="font-semibold text-[#0E1A33]">{h.dates_label}</p>
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {h.status === "past" ? (h.ended === "ended_early" ? "Ended Early" : h.ended === "cancelled" ? "Cancelled" : "Over") : `Booking Reopens ${h.resume_label}`}
                </p>
              </div>
            ),
          },
          {
            header: "Centers",
            accessor: (h) => (h.scope === "all" ? "All Centers" : h.centers.map((c) => c.name).join(", ")),
          },
          { header: "Message", accessor: (h) => <span className="line-clamp-2 max-w-[280px] text-sm">{h.message}</span> },
          { header: "Popup From", accessor: (h) => (h.notice_from ? formatDateTime(h.notice_from) : "—") },
          {
            header: "Status",
            accessor: (h) => (
              <Badge tone={h.status === "active" ? "warning" : h.status === "upcoming" ? "info" : "neutral"}>
                {h.status === "active" ? "Running" : h.status === "upcoming" ? "Upcoming" : "Past"}
              </Badge>
            ),
          },
          {
            header: "",
            accessor: (h) =>
              h.status === "past" ? null : (
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" aria-label={`Edit ${h.dates_label}`} title="Edit" onClick={() => openEdit(h)}>
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button size="sm" variant="outline" className="whitespace-nowrap" isLoading={end.isPending && end.variables?.id === h.id} onClick={() => void askEnd(h)}>
                    <Power className="h-3.5 w-3.5" /> {h.status === "active" ? "End Holiday Now" : "Cancel Holiday"}
                  </Button>
                </div>
              ),
          },
        ]}
      />
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}

      <Modal open={open} onClose={close} title={editing ? "Edit Holiday" : "Add Holiday"} maxWidth="max-w-4xl">
        <form
          className="grid gap-6 md:grid-cols-[minmax(0,1fr)_380px]"
          onSubmit={(e) => {
            e.preventDefault();
            setFormError("");
            save.mutate();
          }}
        >
          <div className="min-w-0 space-y-4">
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Where</p>
              <div className="flex flex-wrap gap-2">
                {(["all", "centers"] as const).map((scope) => (
                  <button
                    key={scope}
                    type="button"
                    aria-pressed={form.scope === scope}
                    onClick={() => setForm((f) => ({ ...f, scope }))}
                    className={`rounded-[10px] border px-3.5 py-2 text-sm font-medium transition ${form.scope === scope ? "border-[#0E1A33] bg-[#FFF4CD] text-[#0E1A33]" : "border-[#E4E9F1] bg-white text-[#5F6878] hover:border-[#C9D6EA]"}`}
                  >
                    {scope === "all" ? "All Centers" : "Select Centers"}
                  </button>
                ))}
              </div>
              {form.scope === "centers" && (
                <div className="mt-2 max-h-40 space-y-1 overflow-y-auto rounded-[12px] border border-[#E4E9F1] p-2">
                  {centerOptions.length === 0 && <p className="px-1 text-xs text-[var(--color-text-secondary)]">No active centers.</p>}
                  {centerOptions.map((c) => (
                    <label key={c.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-[#FFFCF0]">
                      <input type="checkbox" checked={form.centerIds.includes(c.id)} onChange={() => toggleCenter(c.id)} className="h-4 w-4 accent-[#0E1A33]" />
                      <span className="text-[#0E1A33]">{c.name}</span>
                      <span className="text-xs text-[var(--color-text-secondary)]">{c.location?.city}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Input label="Start Date" type="date" value={form.startDate} min={todayIST()} onChange={(e) => setForm((f) => ({ ...f, startDate: e.target.value, endDate: f.endDate < e.target.value ? e.target.value : f.endDate }))} required />
              <TimeField label="Start Time" hint="Empty = from 12:00 AM" value={form.startTime} onChange={(v) => setForm((f) => ({ ...f, startTime: v }))} />
              <Input label="End Date" type="date" value={form.endDate} min={form.startDate || todayIST()} onChange={(e) => setForm((f) => ({ ...f, endDate: e.target.value }))} required />
              <TimeField label="End Time" hint="Empty = the whole day" value={form.endTime} onChange={(v) => setForm((f) => ({ ...f, endTime: v }))} />
            </div>

            <Input label="Title (Optional)" value={form.title} maxLength={80} placeholder="e.g. Diwali Break" onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} />
            <div>
              <label htmlFor="holiday-message" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
                Message For Customers
              </label>
              <textarea
                id="holiday-message"
                rows={3}
                maxLength={300}
                value={form.message}
                onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
                placeholder="Our team is celebrating Diwali with family. Happy Diwali!"
                className={TEXTAREA}
                required
              />
              <p className="mt-1 text-right text-xs text-[var(--color-text-secondary)]">{form.message.length}/300</p>
            </div>
            <Input
              label="Show Popup From (Days Before)"
              type="number"
              min={0}
              max={30}
              value={form.noticeDays}
              onChange={(e) => setForm((f) => ({ ...f, noticeDays: e.target.value }))}
              hint="The website popup starts this many days before the holiday and stays until it ends."
            />

            {affected && affected.count > 0 && (
              <div className="rounded-[12px] border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" role="alert">
                <p className="flex items-center gap-1.5 font-semibold">
                  <AlertTriangle className="h-4 w-4" /> {affected.count}
                  {affected.capped ? "+" : ""} Existing Booking{affected.count === 1 ? "" : "s"} Inside These Dates
                </p>
                <p className="mt-0.5 text-xs">They are not cancelled — call these customers or reschedule them.</p>
                <ul className="mt-2 space-y-1 text-xs">
                  {affected.sample.map((b) => (
                    <li key={b.booking_id} className="flex flex-wrap gap-x-1.5">
                      <span className="font-mono-num font-semibold">{b.booking_number}</span>
                      <span>· {b.date} · {b.slot}</span>
                      <span className="text-amber-800">· {b.customer_name}{b.center_name ? ` · ${b.center_name}` : ""}</span>
                    </li>
                  ))}
                </ul>
                {affected.count > affected.sample.length && <p className="mt-1 text-xs">+ {affected.count - affected.sample.length} more</p>}
              </div>
            )}
            {(formError || previewError) && <p className="text-sm text-[var(--color-error)]">{formError || previewError}</p>}
            <Button type="submit" className="w-full" isLoading={save.isPending} disabled={!ready}>
              {editing ? "Save Changes" : "Save Holiday"}
            </Button>
          </div>

          <div className="min-w-0">
            <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Popup Preview</p>
            <div className="rounded-[18px] bg-[#0E1A33]/55 p-3 sm:p-4">
              {previewData ? (
                <div className="relative overflow-hidden rounded-[22px] border border-[#F3E5B5] bg-white px-6 pb-6 pt-8 text-center">
                  <HolidayCard holiday={previewData.holiday.popup} />
                </div>
              ) : (
                <div className="flex h-[300px] flex-col items-center justify-center gap-2 rounded-[22px] bg-white text-center text-sm text-[var(--color-text-secondary)]">
                  <CalendarOff className="h-6 w-6" />
                  {ready ? (preview.isFetching ? "Loading Preview…" : "Fix The Dates To See The Preview") : "Write A Message And Pick The Dates"}
                </div>
              )}
            </div>
            {previewData && (
              <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                Shows on the website from {previewData.holiday.notice_from ? formatDateTime(previewData.holiday.notice_from) : "now"}.
              </p>
            )}
          </div>
        </form>
      </Modal>
    </div>
  );
}

function TimeField({ label, hint, value, onChange }: { label: string; hint: string; value: string; onChange: (v: string) => void }) {
  return (
    <div className="min-w-0">
      <Input label={`${label} (Optional)`} type="time" value={value} onChange={(e) => onChange(e.target.value)} />
      <p className="mt-1 flex items-center justify-between gap-2 text-xs text-[var(--color-text-secondary)]">
        <span>{hint}</span>
        {value && (
          <button type="button" className="font-medium text-[#0E1A33] hover:underline" onClick={() => onChange("")}>
            Clear
          </button>
        )}
      </p>
    </div>
  );
}
