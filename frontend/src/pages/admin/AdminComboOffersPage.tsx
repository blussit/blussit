import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { catalogApi } from "../../api/catalog";
import { adminComboOfferApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import type { ComboOffer } from "../../types";

const emptyForm = {
  name: "",
  description: "",
  service_ids: [] as string[],
  price: 0,
  discounted_price: "",
};

export default function AdminComboOffersPage() {
  const queryClient = useQueryClient();
  const { data: servicesData } = useQuery({ queryKey: ["admin-services-for-combo"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const { data: combos, isLoading } = useQuery({ queryKey: ["admin-combos"], queryFn: () => adminComboOfferApi.list() });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ComboOffer | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const services = servicesData?.data || [];
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-combos"] });
  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const buildPayload = () => ({
    name: form.name,
    description: form.description || undefined,
    service_ids: form.service_ids,
    price: form.price,
    discounted_price: form.discounted_price ? Number(form.discounted_price) : undefined,
  });

  const createMutation = useMutation({
    mutationFn: () => adminComboOfferApi.create(buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminComboOfferApi.update(editing!.id, buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (c: ComboOffer) => adminComboOfferApi.update(c.id, { is_active: !c.is_active }),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminComboOfferApi.remove(id),
    onSuccess: invalidate,
  });

  const toggleServiceInForm = (id: string) =>
    setForm((f) => ({ ...f, service_ids: f.service_ids.includes(id) ? f.service_ids.filter((s) => s !== id) : [...f.service_ids, id] }));

  const openEdit = (combo: ComboOffer) => {
    setEditing(combo);
    setForm({
      name: combo.name,
      description: combo.description || "",
      service_ids: combo.service_ids,
      price: combo.price,
      discounted_price: combo.discounted_price != null ? String(combo.discounted_price) : "",
    });
    setOpen(true);
  };

  const serviceName = (id: string) => services.find((s) => s.id === id)?.name || id;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Combo offers</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Bundle services into a single sellable item at its own price — e.g. "Exterior + Interior + Wax" at ₹899.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add combo
        </Button>
      </div>

      <DataTable<ComboOffer>
        isLoading={isLoading}
        data={combos || []}
        emptyTitle="No combo offers yet"
        columns={[
          { header: "Name", accessor: (c) => c.name },
          { header: "Includes", accessor: (c) => <span className="text-xs">{c.service_ids.map(serviceName).join(", ")}</span> },
          { header: "Price", accessor: (c) => <span className="font-mono-num">₹{c.discounted_price ?? c.price}</span> },
          { header: "Status", accessor: (c) => <Badge tone={c.is_active ? "success" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (c) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(c)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" isLoading={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate(c)}>
                  <Power className={`h-3.5 w-3.5 ${c.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={() => {
                    if (confirm(`Delete "${c.name}"?`)) deleteMutation.mutate(c.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={open} onClose={closeModal} title={editing ? "Edit combo offer" : "Add combo offer"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateMutation.mutate() : createMutation.mutate();
          }}
        >
          <Input label="Combo name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Input label="Description (optional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />

          <div>
            <p className="mb-2 text-sm font-medium text-[var(--color-text-primary)]">Services included (pick at least 2)</p>
            <div className="flex flex-wrap gap-2">
              {services.map((s) => {
                const selected = form.service_ids.includes(s.id);
                return (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => toggleServiceInForm(s.id)}
                    className={`rounded-full border px-3 py-1.5 text-xs font-medium ${
                      selected ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white" : "border-gray-200 text-gray-600"
                    }`}
                  >
                    {s.name}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Input label="Combo price (₹)" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} required />
            <Input
              label="First-time price (₹, optional)"
              type="number"
              value={form.discounted_price}
              onChange={(e) => setForm({ ...form, discounted_price: e.target.value })}
            />
          </div>

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" disabled={form.service_ids.length < 2} isLoading={createMutation.isPending || updateMutation.isPending}>
            {editing ? "Save changes" : "Add combo offer"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
