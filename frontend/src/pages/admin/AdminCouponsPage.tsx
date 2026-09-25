import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { adminCouponApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
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
  const { data, isLoading } = useQuery({ queryKey: ["admin-coupons"], queryFn: () => adminCouponApi.list({ page: 1, page_size: 30 }) });
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
    description: form.description || undefined,
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-coupons"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminCouponApi.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-coupons"] }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Coupons</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Create promotional discounts and offer codes.</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> Add coupon
        </Button>
      </div>

      <DataTable<Coupon>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No coupons yet"
        columns={[
          { header: "Code", accessor: (c) => <span className="font-mono-num font-semibold">{c.code}</span> },
          { header: "Offer", accessor: (c) => (c.offer_kind === "free_addon_with_service" ? "Free add-on" : "Standard") },
          { header: "Type", accessor: (c) => <span className="capitalize">{c.coupon_type}</span> },
          { header: "Value", accessor: (c) => (c.offer_kind === "free_addon_with_service" ? "Configured" : c.coupon_type === "percentage" ? `${c.value}%` : `₹${c.value}`) },
          { header: "Min order", accessor: (c) => `₹${c.min_order_value}` },
          { header: "Valid until", accessor: (c) => new Date(c.valid_until).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) },
          { header: "Status", accessor: (c) => <Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (c) => (
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => openEdit(c)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" isLoading={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate(c)}>
                  <Power className={`h-3.5 w-3.5 ${c.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={async () => {
                    if (await confirm({ title: `Delete coupon "${c.code}"?`, tone: "danger" })) deleteMutation.mutate(c.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={open} onClose={closeModal} title={editingId ? `Edit ${form.code}` : "Add coupon"}>
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
            label="Coupon code"
            value={form.code}
            onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
            disabled={!!editingId}
            hint={editingId ? "Code can't be changed after creation." : undefined}
            required
          />
          <Input label="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional admin note" />
          <Select label="Offer type" value={form.offer_kind} onChange={(e) => setForm({ ...form, offer_kind: e.target.value as "standard" | "free_addon_with_service" })}>
            <option value="standard">Standard discount</option>
            <option value="free_addon_with_service">Free add-on with selected service</option>
          </Select>
          {form.offer_kind === "standard" ? (
            <>
              <Select label="Discount type" value={form.coupon_type} onChange={(e) => setForm({ ...form, coupon_type: e.target.value as "flat" | "percentage" })}>
                <option value="flat">Flat amount</option>
                <option value="percentage">Percentage</option>
              </Select>
              <div className="grid grid-cols-2 gap-3">
                <Input label="Value" type="number" value={form.value} onChange={(e) => setForm({ ...form, value: Number(e.target.value) })} required />
                <Input
                  label="Min order value"
                  type="number"
                  value={form.min_order_value}
                  onChange={(e) => setForm({ ...form, min_order_value: Number(e.target.value) })}
                />
              </div>
            </>
          ) : (
            <div className="space-y-3 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3">
              <Input
                label="Eligible service keywords"
                value={form.eligible_service_keywords}
                onChange={(e) => setForm({ ...form, eligible_service_keywords: e.target.value })}
                placeholder="star, deep cleaning"
                required
              />
              <Input
                label="Free add-on keywords"
                value={form.free_addon_keywords}
                onChange={(e) => setForm({ ...form, free_addon_keywords: e.target.value })}
                placeholder="extra bike wash"
                required
              />
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Input label="Valid from" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} required />
            <Input label="Valid until" type="date" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} required />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={editingId ? updateMutation.isPending : createMutation.isPending}>
            {editingId ? "Save changes" : "Add coupon"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
