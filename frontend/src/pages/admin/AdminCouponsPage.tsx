import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Power, Trash2 } from "lucide-react";
import { adminCouponApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import type { Coupon } from "../../types";

const emptyForm = {
  code: "",
  coupon_type: "flat" as "flat" | "percentage",
  value: 0,
  min_order_value: 0,
  valid_from: "",
  valid_until: "",
};

export default function AdminCouponsPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({ queryKey: ["admin-coupons"], queryFn: () => adminCouponApi.list({ page: 1, page_size: 30 }) });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      adminCouponApi.create({
        ...form,
        // Sent as explicit IST-offset strings, not new Date(...).toISOString()
        // — that parses a plain "YYYY-MM-DD" input as UTC midnight, which is
        // 5.5h later than intended for "start of this IST business day".
        // valid_until is end-of-day (23:59:59), not midnight-start, so a
        // coupon stays valid through its entire stated last day.
        valid_from: `${form.valid_from}T00:00:00+05:30`,
        valid_until: `${form.valid_until}T23:59:59+05:30`,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-coupons"] });
      setOpen(false);
      setForm(emptyForm);
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
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Create promotional discount codes.</p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add coupon
        </Button>
      </div>

      <DataTable<Coupon>
        isLoading={isLoading}
        data={data?.data || []}
        emptyTitle="No coupons yet"
        columns={[
          { header: "Code", accessor: (c) => <span className="font-mono-num font-semibold">{c.code}</span> },
          { header: "Type", accessor: (c) => <span className="capitalize">{c.coupon_type}</span> },
          { header: "Value", accessor: (c) => (c.coupon_type === "percentage" ? `${c.value}%` : `₹${c.value}`) },
          { header: "Min order", accessor: (c) => `₹${c.min_order_value}` },
          { header: "Status", accessor: (c) => <Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (c) => (
              <div className="flex gap-2">
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

      <Modal open={open} onClose={() => setOpen(false)} title="Add coupon">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            createMutation.mutate();
          }}
        >
          <Input label="Coupon code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} required />
          <Select label="Type" value={form.coupon_type} onChange={(e) => setForm({ ...form, coupon_type: e.target.value as "flat" | "percentage" })}>
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
          <div className="grid grid-cols-2 gap-3">
            <Input label="Valid from" type="date" value={form.valid_from} onChange={(e) => setForm({ ...form, valid_from: e.target.value })} required />
            <Input label="Valid until" type="date" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} required />
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending}>
            Add coupon
          </Button>
        </form>
      </Modal>
    </div>
  );
}
