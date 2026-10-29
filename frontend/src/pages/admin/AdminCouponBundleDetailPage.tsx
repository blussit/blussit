import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FilePlus2, History, Pencil, Power, Trash2 } from "lucide-react";
import { couponBundleApi, type BundleCodeRow, type BundleStatus, type CodeFilters } from "../../api/couponBundles";
import { BundleSettingsFields, bundleToForm, formToSettings, type BundleForm } from "../../components/admin/BundleSettingsFields";
import { CouponTabs } from "../../components/admin/CouponTabs";
import { Pager, SearchBox, useDebouncedValue } from "../../components/shared/ListControls";
import { Badge, Button, DataTable, Input, Modal, Select, StatCard } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date";
import { appliesText, benefitText, stateTone } from "./AdminCouponBundlesPage";

const STATUS_OPTIONS: { value: BundleStatus; label: string }[] = [
  { value: "unused", label: "Unused" },
  { value: "partly_used", label: "Partly Used" },
  { value: "used", label: "Used" },
  { value: "expired", label: "Expired" },
  { value: "disabled", label: "Disabled" },
];
const statusTone = { unused: "success", partly_used: "warning", used: "primary", expired: "neutral", disabled: "error" } as const;
const SORTS: { value: string; label: string }[] = [
  { value: "added:-1", label: "Newest Added" },
  { value: "added:1", label: "Oldest Added" },
  { value: "used:-1", label: "Recently Used" },
  { value: "used:1", label: "Longest Unused" },
  { value: "code:1", label: "Code A–Z" },
  { value: "uses:-1", label: "Most Used" },
];

// A picked day as IST instants: start of that day, and start of the next
// (the API's "to" bound is exclusive).
const dayStart = (d: string) => (d ? `${d}T00:00:00+05:30` : undefined);
const dayEnd = (d: string) => {
  if (!d) return undefined;
  const next = new Date(`${d}T12:00:00+05:30`);
  next.setDate(next.getDate() + 1);
  return `${new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(next)}T00:00:00+05:30`;
};

export default function AdminCouponBundleDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();

  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<BundleStatus | "">("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("added:-1");
  const [addedFrom, setAddedFrom] = useState("");
  const [addedTo, setAddedTo] = useState("");
  const [usedFrom, setUsedFrom] = useState("");
  const [usedTo, setUsedTo] = useState("");
  const q = useDebouncedValue(search.trim());
  const [editOpen, setEditOpen] = useState(false);
  const [form, setForm] = useState<BundleForm | null>(null);
  const [formError, setFormError] = useState("");
  const [historyOf, setHistoryOf] = useState<BundleCodeRow | null>(null);

  const [sortBy, sortOrder] = sort.split(":");
  const filters: CodeFilters = {
    status,
    search: q,
    sort_by: sortBy as CodeFilters["sort_by"],
    sort_order: Number(sortOrder) as 1 | -1,
    added_from: dayStart(addedFrom),
    added_to: dayEnd(addedTo),
    used_from: dayStart(usedFrom),
    used_to: dayEnd(usedTo),
  };

  const bundle = useQuery({ queryKey: ["admin-coupon-bundle", id], queryFn: () => couponBundleApi.get(id) });
  const codes = useQuery({
    queryKey: ["admin-coupon-bundle-codes", id, page, filters],
    queryFn: () => couponBundleApi.codes(id, { ...filters, page, page_size: 50 }),
    placeholderData: keepPreviousData,
  });
  const uses = useQuery({ queryKey: ["admin-coupon-code-uses", id, historyOf?.id], queryFn: () => couponBundleApi.uses(id, historyOf!.id), enabled: !!historyOf });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundle", id] });
    queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundle-codes", id] });
    queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundles"] });
  };

  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error("No form");
      const { settings, error } = formToSettings(form);
      if (!settings) throw new Error(error);
      return couponBundleApi.update(id, settings);
    },
    onSuccess: () => {
      setEditOpen(false);
      pushToast({ tone: "success", title: "Bundle Updated", message: "The change applies to every code in this bundle." });
      refresh();
    },
    onError: (err) => setFormError(err instanceof Error && !("response" in err) ? err.message : getErrorMessage(err)),
  });

  const toggleBundle = useMutation({
    mutationFn: (active: boolean) => couponBundleApi.update(id, { is_active: active }),
    onSuccess: (b) => {
      pushToast({ tone: "success", title: b.is_active ? "Bundle Switched On" : "Bundle Switched Off" });
      refresh();
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const toggleCode = useMutation({
    mutationFn: (c: BundleCodeRow) => couponBundleApi.setCodeActive(id, c.id, !c.is_active),
    onSuccess: refresh,
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const remove = useMutation({
    mutationFn: () => couponBundleApi.remove(id),
    onSuccess: (r) => {
      pushToast({ tone: "success", title: r.message });
      queryClient.invalidateQueries({ queryKey: ["admin-coupon-bundles"] });
      navigate("/admin/coupons/bundles");
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const exporting = useMutation({
    mutationFn: () => couponBundleApi.exportCsv(id, filters),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(bundle.data?.name || "coupon-bundle").replace(/[^A-Za-z0-9_-]+/g, "-")}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    },
    onError: (err) => pushToast({ tone: "error", title: "Couldn't Export", message: getErrorMessage(err) }),
  });

  const b = bundle.data;
  const c = b?.counts;
  const pick = (s: BundleStatus | "") => {
    setStatus(s);
    setPage(1);
  };
  const tile = (label: string, value: number | undefined, s: BundleStatus | "", hint?: string) => (
    <StatCard
      label={label}
      value={value == null ? "—" : value.toLocaleString("en-IN")}
      hint={hint}
      onClick={() => pick(status === s ? "" : s)}
      linkLabel="Filter"
      className={status === s && s !== "" ? "border-[#0E1A33]" : undefined}
    />
  );

  if (bundle.isError) {
    return (
      <div className="space-y-4">
        <CouponTabs />
        <p className="text-sm text-[var(--color-error)]">{getErrorMessage(bundle.error)}</p>
        <Link to="/admin/coupons/bundles" className="text-sm underline">Back To Bundles</Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to="/admin/coupons/bundles" className="mb-1 inline-flex items-center gap-1 text-sm text-[var(--color-text-secondary)] hover:text-black">
            <ArrowLeft className="h-4 w-4" /> All Bundles
          </Link>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold text-[var(--color-text-primary)]">
            {b?.name ?? "Bundle"} {b && <Badge tone={stateTone(b.state)}>{b.state}</Badge>}
          </h1>
          {b && (
            <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
              {benefitText(b)} · Applies To {appliesText(b.applies_to)}
              {b.offer_kind === "standard" && b.min_order_value ? ` · Min Order ₹${b.min_order_value}` : ""} · {b.uses_per_coupon === 1 ? "One-Time Codes" : `${b.uses_per_coupon} Uses Per Code`}
              {b.uses_per_coupon > 1 ? ` · ${b.usage_limit_per_user} Per Customer` : ""} · {b.valid_until ? `Expires ${formatDateTime(b.valid_until)}` : "No Expiry"}
            </p>
          )}
        </div>
        {b && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => navigate(`/admin/coupons/bundles/${id}/add`)}>
              <FilePlus2 className="h-4 w-4" /> Add Codes
            </Button>
            <Button variant="outline" isLoading={exporting.isPending} onClick={() => exporting.mutate()}>
              <Download className="h-4 w-4" /> Export CSV
            </Button>
            <Button variant="outline" onClick={() => { setForm(bundleToForm(b)); setFormError(""); setEditOpen(true); }}>
              <Pencil className="h-4 w-4" /> Edit Bundle
            </Button>
            <Button variant="outline" isLoading={toggleBundle.isPending} onClick={() => toggleBundle.mutate(!b.is_active)}>
              <Power className="h-4 w-4" /> {b.is_active ? "Switch Off" : "Switch On"}
            </Button>
            <Button
              variant="outline"
              isLoading={remove.isPending}
              onClick={async () => {
                const ok = await confirm({
                  title: `Delete Bundle "${b.name}"?`,
                  message: c && c.used + c.partly_used > 0
                    ? "Some codes were already used, so the bundle is only switched off and hidden — its history is kept."
                    : "No code has been used, so the bundle and all its codes are deleted for good.",
                  tone: "danger",
                });
                if (ok) remove.mutate();
              }}
            >
              <Trash2 className="h-4 w-4 text-[var(--color-error)]" /> Delete
            </Button>
          </div>
        )}
      </div>
      <CouponTabs />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        {tile("Total Codes", c?.total, "")}
        {tile("Unused", c?.unused, "unused")}
        {tile("Partly Used", c?.partly_used, "partly_used")}
        {tile("Used", c?.used, "used")}
        {tile("Expired", c?.expired, "expired")}
        {tile("Disabled", c?.disabled, "disabled")}
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <SearchBox value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search Code" className="min-w-[200px] flex-1" />
          <Select compact label="Status" value={status} onChange={(e) => pick(e.target.value as BundleStatus | "")} wrapperClassName="w-44">
            <option value="">All Statuses</option>
            {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
          <Select compact label="Sort By" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }} wrapperClassName="w-44">
            {SORTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Input label="Added From" type="date" value={addedFrom} onChange={(e) => { setAddedFrom(e.target.value); setPage(1); }} />
          <Input label="Added To" type="date" value={addedTo} onChange={(e) => { setAddedTo(e.target.value); setPage(1); }} />
          <Input label="Used From" type="date" value={usedFrom} onChange={(e) => { setUsedFrom(e.target.value); setPage(1); }} />
          <Input label="Used To" type="date" value={usedTo} onChange={(e) => { setUsedTo(e.target.value); setPage(1); }} />
        </div>
      </div>

      <DataTable<BundleCodeRow>
        isLoading={codes.isLoading}
        data={codes.data?.data || []}
        error={codes.error}
        onRetry={() => void codes.refetch()}
        emptyTitle="No Codes Match"
        emptyDescription="Try another status, search or date range."
        columns={[
          { header: "Code", accessor: (r) => <span className="font-mono-num font-semibold">{r.code}</span> },
          { header: "Status", accessor: (r) => <Badge tone={statusTone[r.status]}>{r.status_label}</Badge> },
          { header: "Uses", accessor: (r) => <span className="font-mono-num">{r.used_count} / {r.uses_total}</span> },
          {
            header: "Used By",
            accessor: (r) => (r.used_by ? <div><p className="font-medium">{r.used_by.name}</p>{r.used_by.phone && <p className="font-mono-num text-xs text-gray-500">{r.used_by.phone}</p>}</div> : "—"),
          },
          { header: "Booking", accessor: (r) => (r.booking_number ? <span className="font-mono-num">{r.booking_number}</span> : r.booking_id ? "Plan Purchase" : "—") },
          { header: "Used On", accessor: (r) => (r.used_at ? formatDateTime(r.used_at) : "—") },
          { header: "Added On", accessor: (r) => (r.added_at ? formatDateTime(r.added_at) : "—") },
          {
            header: "",
            accessor: (r) => (
              <div className="flex gap-1">
                {r.used_count > 0 && (
                  <Button size="sm" variant="ghost" aria-label={`History of ${r.code}`} title="Use History" onClick={(e) => { e.stopPropagation(); setHistoryOf(r); }}>
                    <History className="h-3.5 w-3.5" />
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={r.is_active ? `Switch ${r.code} off` : `Switch ${r.code} on`}
                  title={r.is_active ? "Switch Off" : "Switch On"}
                  isLoading={toggleCode.isPending && toggleCode.variables?.id === r.id}
                  onClick={(e) => { e.stopPropagation(); toggleCode.mutate(r); }}
                >
                  <Power className={`h-3.5 w-3.5 ${r.is_active ? "" : "opacity-40"}`} />
                </Button>
              </div>
            ),
          },
        ]}
      />
      {codes.data?.meta && <Pager page={page} totalPages={codes.data.meta.total_pages} total={codes.data.meta.total} onPage={setPage} busy={codes.isFetching} />}

      <Modal open={editOpen} onClose={() => setEditOpen(false)} title="Edit Bundle" maxWidth="max-w-2xl">
        {form && (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              setFormError("");
              save.mutate();
            }}
          >
            <p className="rounded-xl bg-gray-50 p-3 text-xs text-gray-600">
              Changes apply to every code in this bundle at once. Uses per coupon can't go below what any code has already used.
            </p>
            <BundleSettingsFields form={form} setForm={setForm} />
            {formError && <p className="text-sm text-[var(--color-error)]">{formError}</p>}
            <Button type="submit" className="w-full" isLoading={save.isPending}>Save Changes</Button>
          </form>
        )}
      </Modal>

      <Modal open={!!historyOf} onClose={() => setHistoryOf(null)} title={historyOf ? `Uses Of ${historyOf.code}` : "Uses"}>
        {uses.isLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <ul className="space-y-2">
            {(uses.data ?? []).map((u, i) => (
              <li key={i} className="rounded-xl border border-[var(--color-card-border)] p-3 text-sm">
                <p className="font-medium">{u.user.name}{u.user.phone ? ` · ${u.user.phone}` : ""}</p>
                <p className="text-xs text-gray-500">{u.booking_number ? `Booking ${u.booking_number}` : "Plan Purchase"} · {u.used_at ? formatDateTime(u.used_at) : ""}</p>
              </li>
            ))}
            {!uses.data?.length && <li className="text-sm text-gray-500">No uses recorded.</li>}
          </ul>
        )}
      </Modal>
    </div>
  );
}
