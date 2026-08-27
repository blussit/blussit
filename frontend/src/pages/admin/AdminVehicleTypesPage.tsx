import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Star, Trash2 } from "lucide-react";
import { vehicleTypeApi } from "../../api/catalog";
import { adminVehicleTypeApi, analyticsApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import type { VehicleTypeOption } from "../../types";

const emptyForm = { name: "", display_order: 0, is_active: true };

function Stat({ label, value, tone, icon }: { label: string; value: number | string; tone?: "error"; icon?: ReactNode }) {
  return (
    <div className="rounded-xl bg-gray-50 p-3">
      <p className="text-xs text-[var(--color-text-secondary)]">{label}</p>
      <p className={`mt-0.5 flex items-center gap-1 font-mono-num text-lg font-bold ${tone === "error" ? "text-[var(--color-error)]" : "text-[var(--color-text-primary)]"}`}>
        {icon}
        {value}
      </p>
    </div>
  );
}

export default function AdminVehicleTypesPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({ queryKey: ["admin-vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const { data: breakdown } = useQuery({ queryKey: ["vehicle-type-breakdown"], queryFn: () => analyticsApi.vehicleTypeBreakdown() });
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<VehicleTypeOption | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [statsFor, setStatsFor] = useState<VehicleTypeOption | null>(null);

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
        onRowClick={(t) => setStatsFor(t)}
        columns={[
          { header: "Name", accessor: (t) => t.name },
          { header: "Order", accessor: (t) => t.display_order },
          { header: "Status", accessor: (t) => <Badge tone={t.is_active ? "success" : "neutral"}>{t.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (t) => (
              <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
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
                  onClick={async () => {
                    if (await confirm({ title: `Delete "${t.name}"?`, message: "Anything still restricted to it will need to be re-tagged.", tone: "danger" })) deleteMutation.mutate(t.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={!!statsFor} onClose={() => setStatsFor(null)} title={statsFor ? `${statsFor.name} — bookings` : ""}>
        {statsFor && (() => {
          const s = breakdown?.find((b) => b.vehicle_type_id === statsFor.id);
          if (!s) return <p className="text-sm text-[var(--color-text-secondary)]">No bookings for this vehicle type yet.</p>;
          return (
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Total bookings" value={s.total_bookings} />
              <Stat label="Completed" value={s.completed_bookings} />
              <Stat label="Delayed" value={s.delayed_count} tone={s.delayed_count > 0 ? "error" : undefined} />
              <Stat label="Avg rating" value={s.avg_rating != null ? s.avg_rating.toFixed(1) : "—"} icon={<Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />} />
              <Stat label="Avg service time" value={s.avg_service_minutes != null ? `${s.avg_service_minutes} min` : "—"} />
              <Stat label="Avg travel time" value={s.avg_travel_minutes != null ? `${s.avg_travel_minutes} min` : "—"} />
              <Stat label="Avg total job time" value={s.avg_total_minutes != null ? `${s.avg_total_minutes} min` : "—"} />
            </div>
          );
        })()}
      </Modal>

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
