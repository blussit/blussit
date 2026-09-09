import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Star, Trash2 } from "lucide-react";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { adminCatalogApi, analyticsApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { useConfirm } from "../../context/ConfirmContext";
import type { Service, VehicleType } from "../../types";

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
  original_price: "",
  vehicle_type_original_prices: {} as Record<string, string>,
  is_addon: false,
  variant_group: "",
  variant_label: "",
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
  const confirm = useConfirm();
  const { data: categories } = useQuery({ queryKey: ["admin-categories"], queryFn: () => catalogApi.categories(false) });
  const { data: services, isLoading } = useQuery({ queryKey: ["admin-services"], queryFn: () => catalogApi.services({ page_size: 50 }) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const { data: breakdown } = useQuery({ queryKey: ["service-breakdown"], queryFn: () => analyticsApi.serviceBreakdown() });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Service | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [statsFor, setStatsFor] = useState<Service | null>(null);

  const [catOpen, setCatOpen] = useState(false);
  const [catName, setCatName] = useState("");
  const [editingCatId, setEditingCatId] = useState<string | null>(null);

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
    original_price: form.original_price ? Number(form.original_price) : undefined,
    vehicle_type_original_prices: toNumberMap(form.vehicle_type_original_prices),
    is_addon: form.is_addon,
    variant_group: form.variant_group.trim() || undefined,
    variant_label: form.variant_label.trim() || undefined,
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
    // Categories were create-only — renaming or removing one meant a DB
    // edit. Same modal now edits; delete guarded by a confirm.
    mutationFn: () => (editingCatId ? adminCatalogApi.updateCategory(editingCatId, { name: catName }) : adminCatalogApi.createCategory({ name: catName })),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-categories"] });
      setCatOpen(false);
      setCatName("");
      setEditingCatId(null);
    },
  });

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: string) => adminCatalogApi.deleteCategory(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-categories"] }),
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
    const origMap: Record<string, string> = {};
    for (const [k, v] of Object.entries(service.vehicle_type_original_prices || {})) origMap[k] = String(v);
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
      original_price: service.original_price != null ? String(service.original_price) : "",
      vehicle_type_original_prices: origMap,
      is_addon: !!service.is_addon,
      variant_group: service.variant_group || "",
      variant_label: service.variant_label || "",
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
        onRowClick={(s) => setStatsFor(s)}
        columns={[
          { header: "Name", accessor: (s) => s.name },
          {
            header: "Price",
            accessor: (s) => (
              <span className="font-mono-num">
                ₹{s.price}
                {s.original_price != null && s.original_price > s.price && <span className="ml-1.5 text-xs text-gray-400 line-through">₹{s.original_price}</span>}
                {s.is_addon && <span className="ml-1.5 text-xs text-[var(--color-text-secondary)]">add-on</span>}
                {s.variant_label && <span className="ml-1.5 text-xs text-[var(--color-text-secondary)]">{s.variant_label}</span>}
              </span>
            ),
          },
          { header: "Duration", accessor: (s) => `${s.duration_minutes} mins` },
          { header: "Vehicle types", accessor: (s) => (s.vehicle_types.length ? s.vehicle_types.map(vehicleTypeName).join(", ") : "All") },
          { header: "Status", accessor: (s) => <Badge tone={s.is_active ? "success" : "neutral"}>{s.is_active ? "Active" : "Inactive"}</Badge> },
          {
            header: "",
            accessor: (s) => (
              <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
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
                  onClick={async () => {
                    if (await confirm({ title: `Delete "${s.name}"?`, tone: "danger" })) deleteMutation.mutate(s.id);
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
          const s = breakdown?.find((b) => b.service_id === statsFor.id);
          if (!s) return <p className="text-sm text-[var(--color-text-secondary)]">No bookings have included this service yet.</p>;
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Total bookings" value={s.total_bookings} />
                <Stat label="Completed" value={s.completed_bookings} />
                <Stat label="Delayed jobs" value={s.delayed_count} tone={s.delayed_count > 0 ? "error" : undefined} />
                <Stat label="Avg rating" value={s.avg_rating != null ? s.avg_rating.toFixed(1) : "—"} icon={<Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />} />
                <Stat label="Avg actual duration" value={s.avg_actual_minutes != null ? `${s.avg_actual_minutes} min` : "—"} />
                <Stat label="Avg expected duration" value={s.avg_expected_minutes != null ? `${s.avg_expected_minutes} min` : "—"} />
                <Stat label="Avg travel time" value={s.avg_travel_minutes != null ? `${s.avg_travel_minutes} min` : "—"} />
                <Stat label="Avg total job time" value={s.avg_total_minutes != null ? `${s.avg_total_minutes} min` : "—"} />
              </div>
              <p className="text-xs text-[var(--color-text-secondary)]">
                Duration figures are per booking that included this service — a booking with multiple services counts toward each of them.
              </p>
            </div>
          );
        })()}
      </Modal>

      <Modal open={catOpen} onClose={() => { setCatOpen(false); setEditingCatId(null); setCatName(""); }} title={editingCatId ? "Edit category" : "Add category"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            createCategoryMutation.mutate();
          }}
        >
          <Input label="Category name" value={catName} onChange={(e) => setCatName(e.target.value)} required />
          <Button type="submit" className="w-full" isLoading={createCategoryMutation.isPending}>
            {editingCatId ? "Save changes" : "Add category"}
          </Button>
        </form>
        {!!categories?.length && (
          <div className="mt-5 border-t border-gray-100 pt-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Existing categories</p>
            <div className="space-y-1.5">
              {categories.map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 text-sm">
                  <span>{c.name}</span>
                  <span className="flex gap-2">
                    <button type="button" className="text-xs font-medium text-[var(--color-primary)] hover:underline" onClick={() => { setEditingCatId(c.id); setCatName(c.name); }}>
                      Rename
                    </button>
                    <button
                      type="button"
                      className="text-xs font-medium text-[var(--color-error)] hover:underline"
                      onClick={async () => {
                        if (await confirm({ title: `Delete "${c.name}"?`, message: "Services in it keep working but lose their category grouping.", tone: "danger" }))
                          deleteCategoryMutation.mutate(c.id);
                      }}
                    >
                      Delete
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
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
          <div className="w-full">
            <label htmlFor="service-description" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
              What's included
            </label>
            <textarea
              id="service-description"
              rows={4}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={"One item per line, e.g.\nExterior foam wash\nTyre & rim cleaning\nWindow wipe"}
              className="w-full rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm text-[var(--color-text-primary)] placeholder:text-gray-400 transition-colors focus:border-[var(--color-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            />
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
              One item per line shows as a checklist on the website's service card. A single sentence shows as plain text.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Input label="Selling price (₹)" type="number" value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} required />
            <Input
              label="Actual price (₹)"
              type="number"
              hint="Shown struck through on the website; never charged"
              value={form.original_price}
              onChange={(e) => setForm({ ...form, original_price: e.target.value })}
            />
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
          <div className="rounded-xl border border-dashed border-gray-300 p-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300"
                checked={form.is_addon}
                onChange={(e) => setForm({ ...form, is_addon: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">Add-on</span>
                <span className="block text-xs text-[var(--color-text-secondary)]">
                  Optional extra offered on top of a main service (e.g. Exterior Polish +₹200). Not shown as its own card on the website.
                </span>
              </span>
            </label>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Input
                label="Variant group"
                placeholder="e.g. bike-wash"
                hint="Services sharing a group show as one card with a chooser"
                value={form.variant_group}
                onChange={(e) => setForm({ ...form, variant_group: e.target.value })}
              />
              <Input
                label="Variant label"
                placeholder="e.g. 2 bikes"
                value={form.variant_label}
                onChange={(e) => setForm({ ...form, variant_label: e.target.value })}
              />
            </div>
          </div>
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
                blank falls back to the selling / first-time / actual price above. Columns: selling, first-time, actual.
              </p>
              <div className="space-y-2">
                {form.vehicle_types.map((vt) => (
                  <div key={vt} className="grid grid-cols-4 items-center gap-2">
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
                    <Input
                      placeholder={form.original_price ? `₹${form.original_price}` : "Actual"}
                      type="number"
                      value={form.vehicle_type_original_prices[vt] || ""}
                      onChange={(e) => setForm({ ...form, vehicle_type_original_prices: { ...form.vehicle_type_original_prices, [vt]: e.target.value } })}
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
