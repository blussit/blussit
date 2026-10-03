import { useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { adminCouponApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { Pager } from "../../components/shared/ListControls";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { Coupon } from "../../types";

const emptyForm = {
  code: "",
  description: "",
  offer_kind: "standard" as "standard" | "free_addon_with_service",
  coupon_type: "flat" as "flat" | "percentage",
  value: 0,
  min_order_value: 0,
  eligible_service_keywords: "",
  free_addon_keywords: "",
  valid_from: "",
  valid_until: "",
};

export default function AdminCouponsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { push: pushToast } = useToast();
  const [page, setPage] = useState(1);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin-coupons", page],
    queryFn: () => adminCouponApi.list({ page, page_size: 30 }),
    placeholderData: keepPreviousData,
  });
  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const closeModal = () => {
    setOpen(false);
    setEditingId(null);
    setForm(emptyForm);
    setError("");
  };

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm);
    setOpen(true);
  };

  // ISO strings from the API come back with a full timestamp (and whatever
  // offset the DB stored) — the date <input> only ever wants the
  // YYYY-MM-DD part, same convention the create form's own comment below
  // documents for the other direction.
  const openEdit = (c: Coupon) => {
    setEditingId(c.id);
    setForm({
      code: c.code,
      description: c.description || "",
      offer_kind: c.offer_kind || "standard",
      coupon_type: c.coupon_type,
      value: c.value,
      min_order_value: c.min_order_value,
      eligible_service_keywords: (c.eligible_service_keywords || []).join(", "),
      free_addon_keywords: (c.free_addon_keywords || []).join(", "),
      valid_from: c.valid_from.slice(0, 10),
      valid_until: c.valid_until.slice(0, 10),
    });
    setOpen(true);
  };

  const buildPayload = () => ({
    // null (not omitted) so a cleared description actually clears.
    description: form.description.trim() || null,
    offer_kind: form.offer_kind,
    coupon_type: form.coupon_type,
    value: form.offer_kind === "free_addon_with_service" ? 0 : form.value,
    min_order_value: form.min_order_value,
    eligible_service_keywords: form.offer_kind === "free_addon_with_service" ? form.eligible_service_keywords.split(",").map((x) => x.trim()).filter(Boolean) : [],
    free_addon_keywords: form.offer_kind === "free_addon_with_service" ? form.free_addon_keywords.split(",").map((x) => x.trim()).filter(Boolean) : [],
    // Sent as explicit IST-offset strings, not new Date(...).toISOString()
    // — that parses a plain "YYYY-MM-DD" input as UTC midnight, which is
    // 5.5h later than intended for "start of this IST business day".
    // valid_until is end-of-day (23:59:59), not midnight-start, so a
    // coupon stays valid through its entire stated last day.
    valid_from: `${form.valid_from}T00:00:00+05:30`,
    valid_until: `${form.valid_until}T23:59:59+05:30`,
  });

  const createMutation = useMutation({
    mutationFn: () => adminCouponApi.create({ code: form.code, ...buildPayload() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminCouponApi.update(editingId as string, buildPayload()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (c: Coupon) => adminCouponApi.update(c.id, { is_active: !c.is_active }),
    onSuccess: (c) => {
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
      pushToast({ tone: "success", title: c.is_active ? `${c.code} is live` : `${c.code} switched off` });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminCouponApi.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
      pushToast({ tone: "success", title: "Coupon deleted" });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Coupons</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Create promotional discounts and offer codes.</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> Add Coupon
        </Button>
      </div>

      <DataTable<Coupon>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No Coupons Yet"
        columns={[
          { header: "Code", accessor: (c) => <span className="font-mono-num font-semibold">{c.code}</span> },
          { header: "Offer", accessor: (c) => (c.offer_kind === "free_addon_with_service" ? "Free Add-On" : "Standard") },
          { header: "Type", accessor: (c) => <span className="capitalize">{c.coupon_type}</span> },
          { header: "Value", accessor: (c) => (c.offer_kind === "free_addon_with_service" ? "Configured" : c.coupon_type === "percentage" ? `${c.value}%` : `₹${c.value}`) },
          { header: "Min Order", accessor: (c) => `₹${c.min_order_value}` },
          { header: "Valid Until", accessor: (c) => format(c.valid_until) },
          { header: "Status", accessor: (c) => <Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (c) => (
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" aria-label={`Edit ${c.code}`} title="Edit" onClick={() => openEdit(c)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={c.is_active ? `Switch ${c.code} off` : `Switch ${c.code} on`}
                  title={c.is_active ? "Switch off" : "Switch on"}
                  isLoading={toggleActiveMutation.isPending && toggleActiveMutation.variables?.id === c.id}
                  onClick={() => toggleActiveMutation.mutate(c)}
                >
                  <Power className={`h-3.5 w-3.5 ${c.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete ${c.code}`}
                  title="Delete"
                  isLoading={deleteMutation.isPending && deleteMutation.variables === c.id}
                  onClick={async () => {
                    if (await confirm({ title: `Delete Coupon "${c.code}"?`, tone: "danger" })) deleteMutation.mutate(c.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}

      <Modal open={open} onClose={closeModal} title={editingId ? `Edit ${form.code}` : "Add Coupon"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            if (editingId) updateMutation.mutate();
            else createMutation.mutate();
          }}
        >
          <Input
            label="Coupon Code"
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
            disabled={!!editingId}
            hint={editingId ? "Code can't be changed after creation." : undefined}
            required
          />
          <Input label="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional admin note" />
          <Select label="Offer Type" value={form.offer_kind} onChange={(e) => setForm({ ...form, offer_kind: e.target.value as "standard" | "free_addon_with_service" })}>
            <option value="standard">Standard Discount</option>
            <option value="free_addon_with_service">Free Add-On With Selected Service</option>
          </Select>
          {form.offer_kind === "standard" ? (
            <>
              <Select label="Discount Type" value={form.coupon_type} onChange={(e) => setForm({ ...form, coupon_type: e.target.value as "flat" | "percentage" })}>
                <option value="flat">Flat Amount</option>
                <option value="percentage">Percentage</option>
              </Select>
              <div className="grid grid-cols-2 gap-3">
                <Input label="Value" type="number" value={form.value} onChange={(e) => setForm({ ...form, value: Number(e.target.value) })} required />
                <Input
                  label="Min Order Value"
                  type="number"
                  value={form.min_order_value}
                  onChange={(e) => setForm({ ...form, min_order_value: Number(e.target.value) })}
                />
              </div>
            </>
          ) : (
            <div className="space-y-3 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3">
              <Input
                label="Eligible Service Keywords"
                value={form.eligible_service_keywords}
                onChange={(e) => setForm({ ...form, eligible_service_keywords: e.target.value })}
                placeholder="star, deep cleaning"
                required
              />
              <Input
                label="Free Add-On Keywords"
                value={form.free_addon_keywords}
                onChange={(e) => setForm({ ...form, free_addon_keywords: e.target.value })}
                placeholder="extra bike wash"
                required
              />
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Input label="Valid From" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} required />
            <Input label="Valid Until" type="date" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} required />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={editingId ? updateMutation.isPending : createMutation.isPending}>
            {editingId ? "Save Changes" : "Add Coupon"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
