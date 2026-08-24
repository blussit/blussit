import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { adminCatalogApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import type { Service, VehicleType } from "../../types";

const emptyForm = {
  category_id: "",
  name: "",
  description: "",
  price: 0,
  discounted_price: "",
  duration_minutes: 30,
  vehicle_types: [] as VehicleType[],
  captain_fee: undefined as number | undefined,
  vehicle_type_prices: {} as Record<string, string>,
  vehicle_type_discounted_prices: {} as Record<string, string>,
};

function toNumberMap(input: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(input)) {
    if (v.trim() !== "") out[k] = Number(v);
  }
  return out;
}

export default function AdminServicesPage() {
  const queryClient = useQueryClient();
  const { data: categories } = useQuery({ queryKey: ["admin-categories"], queryFn: () => catalogApi.categories(false) });
  const { data: services, isLoading } = useQuery({ queryKey: ["admin-services"], queryFn: () => catalogApi.services({ page_size: 50 }) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Service | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const [catOpen, setCatOpen] = useState(false);
  const [catName, setCatName] = useState("");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-services"] });
  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const buildPayload = () => ({
    category_id: form.category_id,
    name: form.name,
    description: form.description || undefined,
    price: form.price,
    discounted_price: form.discounted_price ? Number(form.discounted_price) : undefined,
    duration_minutes: form.duration_minutes,
    vehicle_types: form.vehicle_types,
    captain_fee: form.captain_fee,
    vehicle_type_prices: toNumberMap(form.vehicle_type_prices),
    vehicle_type_discounted_prices: toNumberMap(form.vehicle_type_discounted_prices),
  });

  const createServiceMutation = useMutation({
    mutationFn: () => adminCatalogApi.createService(buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateServiceMutation = useMutation({
    mutationFn: () => adminCatalogApi.updateService(editing!.id, buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (s: Service) => adminCatalogApi.updateService(s.id, { is_active: !s.is_active }),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminCatalogApi.deleteService(id),
    onSuccess: invalidate,
  });

  const createCategoryMutation = useMutation({
    mutationFn: () => adminCatalogApi.createCategory({ name: catName }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-categories"] });
      setCatOpen(false);
      setCatName("");
    },
  });

  const toggleVehicleType = (vt: VehicleType) => {
    setForm((f) => ({
      ...f,
      vehicle_types: f.vehicle_types.includes(vt) ? f.vehicle_types.filter((v) => v !== vt) : [...f.vehicle_types, vt],
    }));
  };

  const openEdit = (service: Service) => {
    setEditing(service);
    const priceMap: Record<string, string> = {};
    const discMap: Record<string, string> = {};
    for (const [k, v] of Object.entries(service.vehicle_type_prices || {})) priceMap[k] = String(v);
    for (const [k, v] of Object.entries(service.vehicle_type_discounted_prices || {})) discMap[k] = String(v);
    setForm({
      category_id: service.category_id,
      name: service.name,
      description: service.description || "",
      price: service.price,
      discounted_price: service.discounted_price != null ? String(service.discounted_price) : "",
      duration_minutes: service.duration_minutes,
      vehicle_types: service.vehicle_types,
      captain_fee: service.captain_fee ?? undefined,
      vehicle_type_prices: priceMap,
      vehicle_type_discounted_prices: discMap,
    });
    setOpen(true);
  };

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Services</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage the service catalog customers can book.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setCatOpen(true)}>
            <Plus className="h-4 w-4" /> Add category
          </Button>
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" /> Add service
          </Button>
        </div>
      </div>

      <DataTable<Service>
        isLoading={isLoading}
        data={services?.data || []}
        emptyTitle="No services yet"
        columns={[
          { header: "Name", accessor: (s) => s.name },
          { header: "Price", accessor: (s) => <span className="font-mono-num">₹{s.discounted_price ?? s.price}</span> },
          { header: "Duration", accessor: (s) => `${s.duration_minutes} mins` },
          { header: "Vehicle types", accessor: (s) => (s.vehicle_types.length ? s.vehicle_types.map(vehicleTypeName).join(", ") : "All") },
          { header: "Status", accessor: (s) => <Badge tone={s.is_active ? "success" : "neutral"}>{s.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (s) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(s)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" isLoading={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate(s)}>
                  <Power className={`h-3.5 w-3.5 ${s.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={() => {
                    if (confirm(`Delete "${s.name}"?`)) deleteMutation.mutate(s.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={catOpen} onClose={() => setCatOpen(false)} title="Add category">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            createCategoryMutation.mutate();
          }}
        >
          <Input label="Category name" value={catName} onChange={(e) => setCatName(e.target.value)} required />
          <Button type="submit" className="w-full" isLoading={createCategoryMutation.isPending}>
            Add category
          </Button>
        </form>
      </Modal>

      <Modal open={open} onClose={closeModal} title={editing ? "Edit service" : "Add service"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateServiceMutation.mutate() : createServiceMutation.mutate();
          }}
        >
          <Select label="Category" value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} required>
            <option value="">Choose a category</option>
            {(categories || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <Input label="Service name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Input label="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <div className="grid grid-cols-3 gap-3">
            <Input label="Regular price (₹)" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} required />
            <Input
              label="First-time price (₹)"
              type="number"
              hint="Only applied if this exact vehicle & phone have no prior booking"
              value={form.discounted_price}
              onChange={(e) => setForm({ ...form, discounted_price: e.target.value })}
            />
            <Input
              label="Duration (mins)"
              type="number"
              value={form.duration_minutes}
              onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })}
              required
            />
          </div>
          <Input
            label="Captain fee override (₹, optional)"
            type="number"
            hint="Leave blank to use the platform's default captain fee from Pricing & wallets."
            value={form.captain_fee ?? ""}
            onChange={(e) => setForm({ ...form, captain_fee: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Applicable vehicle types</p>
            <div className="flex flex-wrap gap-2">
              {(vehicleTypes || []).map((t) => (
                <button
                  type="button"
                  key={t.id}
                  onClick={() => toggleVehicleType(t.id)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                    form.vehicle_types.includes(t.id) ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {t.name}
                </button>
              ))}
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Leave all unselected to allow every vehicle type.</p>
          </div>

          {form.vehicle_types.length > 0 && (
            <div className="rounded-xl border border-dashed border-gray-300 p-4">
              <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Per-vehicle-type pricing (optional)</p>
              <p className="mb-3 text-xs text-[var(--color-text-secondary)]">
                A hatchback wash and a luxury SUV wash aren't the same job — override the price for any type here; anything left
                blank falls back to the regular/first-time price above.
              </p>
              <div className="space-y-2">
                {form.vehicle_types.map((vt) => (
                  <div key={vt} className="grid grid-cols-3 items-center gap-2">
                    <span className="text-sm font-medium text-[var(--color-text-primary)]">{vehicleTypeName(vt)}</span>
                    <Input
                      placeholder={`₹${form.price || 0}`}
                      type="number"
                      value={form.vehicle_type_prices[vt] || ""}
                      onChange={(e) => setForm({ ...form, vehicle_type_prices: { ...form.vehicle_type_prices, [vt]: e.target.value } })}
                    />
                    <Input
                      placeholder={form.discounted_price ? `₹${form.discounted_price}` : "First-time"}
                      type="number"
                      value={form.vehicle_type_discounted_prices[vt] || ""}
                      onChange={(e) => setForm({ ...form, vehicle_type_discounted_prices: { ...form.vehicle_type_discounted_prices, [vt]: e.target.value } })}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button type="submit" className="w-full" isLoading={createServiceMutation.isPending || updateServiceMutation.isPending}>
            {editing ? "Save changes" : "Add service"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
