import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { vehicleTypeApi } from "../../api/catalog";
import { adminVehicleTypeApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import type { VehicleTypeOption } from "../../types";

const emptyForm = { name: "", display_order: 0, is_active: true };

export default function AdminVehicleTypesPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["admin-vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<VehicleTypeOption | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["admin-vehicle-types"] });
    // Every vehicle/service/plan/booking picker across the app reads this
    // same cached list — keep them all in sync with admin's changes.
    queryClient.invalidateQueries({ queryKey: ["vehicle-types"] });
  };
  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const createMutation = useMutation({
    mutationFn: () => adminVehicleTypeApi.create(form),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminVehicleTypeApi.update(editing!.id, form),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (t: VehicleTypeOption) => adminVehicleTypeApi.update(t.id, { is_active: !t.is_active }),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminVehicleTypeApi.remove(id),
    onSuccess: invalidate,
  });

  const openEdit = (t: VehicleTypeOption) => {
    setEditing(t);
    setForm({ name: t.name, display_order: t.display_order, is_active: t.is_active });
    setOpen(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Vehicle types</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            The body types services, combos, and subscription plans can be restricted to (Hatchback, Sedan, SUV, ...).
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add type
        </Button>
      </div>

      <DataTable<VehicleTypeOption>
        isLoading={isLoading}
        data={data || []}
        emptyTitle="No vehicle types yet"
        columns={[
          { header: "Name", accessor: (t) => t.name },
          { header: "Order", accessor: (t) => t.display_order },
          { header: "Status", accessor: (t) => <Badge tone={t.is_active ? "success" : "neutral"}>{t.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (t) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(t)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" isLoading={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate(t)}>
                  <Power className={`h-3.5 w-3.5 ${t.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={() => {
                    if (confirm(`Delete "${t.name}"? Anything still restricted to it will need to be re-tagged.`)) deleteMutation.mutate(t.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={open} onClose={closeModal} title={editing ? "Edit vehicle type" : "Add vehicle type"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateMutation.mutate() : createMutation.mutate();
          }}
        >
          <Input label="Name" placeholder="e.g. XUV 7-Seater" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Input
            label="Display order"
            type="number"
            value={form.display_order}
            onChange={(e) => setForm({ ...form, display_order: Number(e.target.value) })}
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createMutation.isPending || updateMutation.isPending}>
            {editing ? "Save changes" : "Add vehicle type"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
