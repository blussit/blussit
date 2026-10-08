import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Star, Trash2 } from "lucide-react";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { adminCatalogApi, analyticsApi } from "../../api/admin";
import { Badge, Button, DataTable, ErrorState, Input, Modal, Select } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { toTitle } from "../../lib/titleCase";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
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
  is_waterless: false,
  variant_group: "",
  variant_label: "",
  prepaid_only: false,
  charges_travel: false,
  offer_tag: "",
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
  const { push: pushToast } = useToast();
  const categoriesQuery = useQuery({ queryKey: ["admin-categories"], queryFn: () => catalogApi.categories(false) });
  const categories = categoriesQuery.data;
  // active_only=false: a service switched OFF must stay on this page (with
  // its Inactive badge) so it can be switched back on or edited.
  const { data: services, isLoading, error: servicesError, refetch: refetchServices } = useQuery({
    queryKey: ["admin-services"],
    queryFn: () => catalogApi.services({ page_size: 100, active_only: false }),
  });
  const vehicleTypesQuery = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const vehicleTypes = vehicleTypesQuery.data;
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const breakdownQuery = useQuery({ queryKey: ["service-breakdown"], queryFn: () => analyticsApi.serviceBreakdown() });
  const breakdown = breakdownQuery.data;

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

  // Per-type overrides only for the types still selected — a deselected
  // type's hidden price used to stay live (prices are read by key).
  const selectedOnly = (m: Record<string, string>) =>
    toNumberMap(form.vehicle_types.length ? Object.fromEntries(Object.entries(m).filter(([k]) => form.vehicle_types.includes(k as VehicleType))) : {});
  // Optional prices / variant fields go as null when blank: the API only
  // clears a field it receives as null (undefined keys vanish from JSON, so
  // a removed first-time price used to keep undercutting the offer price).
  const buildPayload = () => ({
    category_id: form.category_id,
    name: form.name,
    description: form.description || undefined,
    price: form.price,
    discounted_price: form.discounted_price ? Number(form.discounted_price) : null,
    duration_minutes: form.duration_minutes,
    vehicle_types: form.vehicle_types,
    captain_fee: form.captain_fee ?? null,
    vehicle_type_prices: selectedOnly(form.vehicle_type_prices),
    vehicle_type_discounted_prices: selectedOnly(form.vehicle_type_discounted_prices),
    original_price: form.original_price ? Number(form.original_price) : null,
    vehicle_type_original_prices: selectedOnly(form.vehicle_type_original_prices),
    is_addon: form.is_addon,
    is_waterless: form.is_waterless,
    variant_group: form.variant_group.trim() || null,
    variant_label: form.variant_label.trim() || null,
    prepaid_only: form.prepaid_only,
    charges_travel: form.charges_travel,
    // "" (not null) so clearing the tag sticks — the update endpoint drops null fields.
    offer_tag: form.offer_tag.trim(),
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
    onSuccess: (s) => {
      invalidate();
      pushToast({ tone: "success", title: s.is_active ? `${s.name} is on sale again` : `${s.name} switched off` });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminCatalogApi.deleteService(id),
    onSuccess: () => {
      invalidate();
      pushToast({ tone: "success", title: "Service deleted" });
    },
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
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
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
  });

  const deleteCategoryMutation = useMutation({
    mutationFn: (id: string) => adminCatalogApi.deleteCategory(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-categories"] }),
    onError: (err) => pushToast({ tone: "error", title: getErrorMessage(err) }),
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
      is_waterless: !!service.is_waterless,
      variant_group: service.variant_group || "",
      variant_label: service.variant_label || "",
      prepaid_only: !!service.prepaid_only,
      charges_travel: !!service.charges_travel,
      offer_tag: service.offer_tag || "",
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
            <Plus className="h-4 w-4" /> Add Category
          </Button>
          <Button onClick={() => setOpen(true)}>
            <Plus className="h-4 w-4" /> Add Service
          </Button>
        </div>
      </div>

      <DataTable<Service>
        isLoading={isLoading}
        data={services?.data || []}
        error={servicesError}
        onRetry={() => void refetchServices()}
        emptyTitle="No Services Yet"
        onRowClick={(s) => setStatsFor(s)}
        columns={[
          {
            header: "Name",
            accessor: (s) => (
              <span>
                {toTitle(s.name)}
                {(s.offer_tag || s.prepaid_only || s.charges_travel) && (
                  <span className="mt-1 flex flex-wrap gap-1">
                    {s.offer_tag && <Badge tone="info" className="px-2 py-0.5 text-[10px]">{toTitle(s.offer_tag)}</Badge>}
                    {s.prepaid_only && <Badge className="px-2 py-0.5 text-[10px]">Prepaid</Badge>}
                    {s.charges_travel && <Badge className="px-2 py-0.5 text-[10px]">Distance Charge</Badge>}
                  </span>
                )}
              </span>
            ),
          },
          {
            header: "Price",
            accessor: (s) => (
              <span className="font-mono-num">
                ₹{s.price}
                {s.original_price != null && s.original_price > s.price && <span className="ml-1.5 text-xs text-gray-400 line-through">₹{s.original_price}</span>}
                {s.is_addon && <span className="ml-1.5 text-xs text-[var(--color-text-secondary)]">Add-On</span>}
                {s.variant_label && <span className="ml-1.5 text-xs text-[var(--color-text-secondary)]">{toTitle(s.variant_label)}</span>}
              </span>
            ),
          },
          { header: "Duration", accessor: (s) => `${s.duration_minutes} mins` },
          { header: "Vehicle Types", accessor: (s) => (s.vehicle_types.length ? s.vehicle_types.map((v) => toTitle(vehicleTypeName(v))).join(", ") : "All") },
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
                    if (await confirm({ title: `Delete "${toTitle(s.name)}"?`, tone: "danger" })) deleteMutation.mutate(s.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={!!statsFor} onClose={() => setStatsFor(null)} title={statsFor ? `${toTitle(statsFor.name)} — Bookings` : ""}>
        {statsFor && (() => {
          if (breakdownQuery.isError && !breakdown)
            return (
              <ErrorState
                message="Couldn't load this service's booking stats."
                onRetry={() => void breakdownQuery.refetch()}
                busy={breakdownQuery.isFetching}
                className="p-4"
              />
            );
          const s = breakdown?.find((b) => b.service_id === statsFor.id);
          if (!s) return <p className="text-sm text-[var(--color-text-secondary)]">No bookings have included this service yet.</p>;
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Total Bookings" value={s.total_bookings} />
                <Stat label="Completed" value={s.completed_bookings} />
                <Stat label="Delayed Jobs" value={s.delayed_count} tone={s.delayed_count > 0 ? "error" : undefined} />
                <Stat label="Avg Rating" value={s.avg_rating != null ? s.avg_rating.toFixed(1) : "—"} icon={<Star className="h-3.5 w-3.5 fill-[var(--color-secondary)] text-[var(--color-secondary)]" />} />
                <Stat label="Avg Actual Duration" value={s.avg_actual_minutes != null ? `${s.avg_actual_minutes} min` : "—"} />
                <Stat label="Avg Expected Duration" value={s.avg_expected_minutes != null ? `${s.avg_expected_minutes} min` : "—"} />
                <Stat label="Avg Travel Time" value={s.avg_travel_minutes != null ? `${s.avg_travel_minutes} min` : "—"} />
                <Stat label="Avg Total Job Time" value={s.avg_total_minutes != null ? `${s.avg_total_minutes} min` : "—"} />
              </div>
              <p className="text-xs text-[var(--color-text-secondary)]">
                Duration figures are per booking that included this service — a booking with multiple services counts toward each of them.
              </p>
            </div>
          );
        })()}
      </Modal>

      <Modal open={catOpen} onClose={() => { setCatOpen(false); setEditingCatId(null); setCatName(""); }} title={editingCatId ? "Edit Category" : "Add Category"}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            createCategoryMutation.mutate();
          }}
        >
          <Input label="Category Name" value={catName} onChange={(e) => setCatName(e.target.value)} required />
          <Button type="submit" className="w-full" isLoading={createCategoryMutation.isPending}>
            {editingCatId ? "Save Changes" : "Add Category"}
          </Button>
        </form>
        {!!categories?.length && (
          <div className="mt-5 border-t border-gray-100 pt-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Existing Categories</p>
            <div className="space-y-1.5">
              {categories.map((c) => (
                <div key={c.id} className="flex items-center justify-between gap-3 rounded-lg border border-gray-100 px-3 py-2 text-sm">
                  <span>{toTitle(c.name)}</span>
                  <span className="flex gap-2">
                    <button type="button" className="text-xs font-medium text-[var(--color-primary)] hover:underline" onClick={() => { setEditingCatId(c.id); setCatName(c.name); }}>
                      Rename
                    </button>
                    <button
                      type="button"
                      className="text-xs font-medium text-[var(--color-error)] hover:underline"
                      onClick={async () => {
                        if (await confirm({ title: `Delete "${toTitle(c.name)}"?`, message: "Services in it keep working but lose their category grouping.", tone: "danger" }))
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

      <Modal open={open} onClose={closeModal} title={editing ? "Edit Service" : "Add Service"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateServiceMutation.mutate() : createServiceMutation.mutate();
          }}
        >
          <Select label="Category" value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })} required>
            <option value="">Choose A Category</option>
            {(categories || []).map((c) => (
              <option key={c.id} value={c.id}>
                {toTitle(c.name)}
              </option>
            ))}
          </Select>
          {categoriesQuery.isError && !categories && (
            <ErrorState message="Couldn't load categories." onRetry={() => void categoriesQuery.refetch()} busy={categoriesQuery.isFetching} className="p-4" />
          )}
          <Input label="Service Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <div className="w-full">
            <label htmlFor="service-description" className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">
              What's Included
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
            <Input label="Selling Price (₹)" type="number" step="any" value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} required />
            <Input
              label="Actual Price (₹)"
              type="number"
              step="any"
              hint="Shown struck through on the website; never charged"
              value={form.original_price}
              onChange={(e) => setForm({ ...form, original_price: e.target.value })}
            />
            <Input
              label="First-Time Price (₹)"
              type="number"
              step="any"
              hint="Only applied if this exact vehicle & phone have no prior booking"
              value={form.discounted_price}
              onChange={(e) => setForm({ ...form, discounted_price: e.target.value })}
            />
            <Input
              label="Duration (Mins)"
              type="number"
              value={form.duration_minutes}
              onChange={(e) => setForm({ ...form, duration_minutes: Number(e.target.value) })}
              required
            />
          </div>
          <Input
            label="Captain Fee Override (₹, Optional)"
            type="number"
            step="any"
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
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">Add-On</span>
                <span className="block text-xs text-[var(--color-text-secondary)]">
                  Optional extra offered on top of a main service (e.g. Exterior Polish +₹200). Not shown as its own card on the website.
                </span>
              </span>
            </label>
            {/* Drives what the customer is told to prepare at booking —
                water and power for a water-based wash, shade for a
                waterless one. Getting this wrong sends a captain to a
                vehicle with no water. */}
            <label className="mt-3 flex cursor-pointer items-start gap-3 border-t border-gray-100 pt-3">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300"
                checked={form.is_waterless}
                onChange={(e) => setForm({ ...form, is_waterless: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">Waterless Wash</span>
                <span className="block text-xs text-[var(--color-text-secondary)]">
                  Customer is asked to park in shade and told the inner wheel area isn't cleaned. Leave off for
                  water-based washes, where they're asked to have water and power ready.
                </span>
              </span>
            </label>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Input
                label="Variant Group"
                placeholder="e.g. bike-wash"
                hint="Services sharing a group show as one card with a chooser"
                value={form.variant_group}
                onChange={(e) => setForm({ ...form, variant_group: e.target.value })}
              />
              <Input
                label="Variant Label"
                placeholder="e.g. 2 bikes"
                value={form.variant_label}
                onChange={(e) => setForm({ ...form, variant_label: e.target.value })}
              />
            </div>
          </div>
          <div className="rounded-xl border border-dashed border-gray-300 p-4">
            <label className="flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300"
                checked={form.prepaid_only}
                onChange={(e) => setForm({ ...form, prepaid_only: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">Prepaid Only (Online Payment Required)</span>
                <span className="block text-xs text-[var(--color-text-secondary)]">
                  The booking is confirmed only once paid online — no cash option. Plan-covered washes are unaffected.
                </span>
              </span>
            </label>
            <label className="mt-3 flex cursor-pointer items-start gap-3 border-t border-gray-100 pt-3">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-gray-300"
                checked={form.charges_travel}
                onChange={(e) => setForm({ ...form, charges_travel: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-medium text-[var(--color-text-primary)]">Charge Distance (Beyond Free Km)</span>
                <span className="block text-xs text-[var(--color-text-secondary)]">
                  Adds the customer distance charge to the visit — free km and ₹ per km are set in Settings & pricing.
                </span>
              </span>
            </label>
            <div className="mt-3 border-t border-gray-100 pt-3">
              <Input
                label="Offer Tag"
                placeholder="e.g. Launch offer"
                maxLength={30}
                hint="Shown on the service card; the first active service with a tag is promoted in the website popup. Leave blank for no offer."
                value={form.offer_tag}
                onChange={(e) => setForm({ ...form, offer_tag: e.target.value })}
              />
            </div>
          </div>
          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Applicable Vehicle Types</p>
            {vehicleTypesQuery.isError && !vehicleTypes && (
              <ErrorState
                message="Couldn't load vehicle types."
                onRetry={() => void vehicleTypesQuery.refetch()}
                busy={vehicleTypesQuery.isFetching}
                className="mb-2 p-4"
              />
            )}
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
                  {toTitle(t.name)}
                </button>
              ))}
            </div>
            <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Leave all unselected to allow every vehicle type.</p>
          </div>

          {form.vehicle_types.length > 0 && (
            <div className="rounded-xl border border-dashed border-gray-300 p-4">
              <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Per-Vehicle-Type Pricing (Optional)</p>
              <p className="mb-3 text-xs text-[var(--color-text-secondary)]">
                A hatchback wash and a luxury SUV wash aren't the same job — override the price for any type here; anything left
                blank falls back to the selling / first-time / actual price above. Columns: selling, first-time, actual.
              </p>
              <div className="space-y-2">
                {form.vehicle_types.map((vt) => (
                  <div key={vt} className="grid grid-cols-4 items-center gap-2">
                    <span className="text-sm font-medium text-[var(--color-text-primary)]">{toTitle(vehicleTypeName(vt))}</span>
                    <Input
                      placeholder={`₹${form.price || 0}`}
                      type="number"
                      step="any"
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
            {editing ? "Save Changes" : "Add Service"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
