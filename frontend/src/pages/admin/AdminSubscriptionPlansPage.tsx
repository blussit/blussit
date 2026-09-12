import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { adminSubscriptionPlanApi } from "../../api/admin";
import { Badge, Button, DataTable, Input, Modal, Select } from "../../components/ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import type { SubscriptionPlan } from "../../types";

const emptyForm = {
  name: "",
  description: "",
  billing_cycle: "monthly" as "monthly" | "quarterly" | "yearly",
  price: 0,
  discounted_price: "",
  vehicle_type_prices: {} as Record<string, string>,
  // "<serviceId>:<vehicleTypeId>" -> flat monthly pass price. A value here
  // is the WHOLE monthly price for that wash type on that vehicle type and
  // beats the computed one; blank falls back to the calculation.
  service_pass_prices: {} as Record<string, string>,
  plan_discount_percent: "",
  included_service_ids: [] as string[],
  total_service_count: 1,
  category_quotas: {} as Record<string, string>,
  vehicle_types: [] as string[],
  upgrade_to_plan_ids: [] as string[],
  is_popular: false,
};

/** The form keeps pass prices flat ("svc:type" -> "999") because a nested
 *  object is miserable to edit in React state; the API wants them nested. */
const passKey = (serviceId: string, typeId: string) => `${serviceId}:${typeId}`;

function nestPassPrices(flat: Record<string, string>): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  for (const [key, value] of Object.entries(flat)) {
    const amount = Number(value);
    if (!value || !Number.isFinite(amount) || amount <= 0) continue; // blank = use the formula
    const [serviceId, typeId] = key.split(":");
    if (!serviceId || !typeId) continue;
    (out[serviceId] ||= {})[typeId] = amount;
  }
  return out;
}

function flattenPassPrices(nested?: Record<string, Record<string, number>>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [serviceId, byType] of Object.entries(nested || {})) {
    for (const [typeId, amount] of Object.entries(byType || {})) out[passKey(serviceId, typeId)] = String(amount);
  }
  return out;
}

function totalOf(quotas: Record<string, string>): number {
  return Object.values(quotas).reduce((sum, v) => sum + (Number(v) || 0), 0);
}

function toNumberMap(input: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(input)) {
    if (v.trim() !== "") out[k] = Number(v);
  }
  return out;
}

export default function AdminSubscriptionPlansPage() {
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const { data, isLoading } = useQuery({ queryKey: ["admin-plans"], queryFn: () => subscriptionApi.plans(false) });
  const { data: categories } = useQuery({ queryKey: ["admin-categories"], queryFn: () => catalogApi.categories(false) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(false) });
  const { data: servicesData } = useQuery({ queryKey: ["admin-services-for-plans"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = servicesData?.data || [];
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<SubscriptionPlan | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-plans"] });
  const closeModal = () => {
    setOpen(false);
    setEditing(null);
    setForm(emptyForm);
    setError("");
  };

  const buildPayload = () => {
    const category_quotas: Record<string, number> = {};
    for (const [catId, val] of Object.entries(form.category_quotas)) {
      if (Number(val) > 0) category_quotas[catId] = Number(val);
    }
    // Category quotas (if set) are the source of truth for the count, same
    // as before — otherwise fall back to the plain "how many visits" field,
    // which is the normal path now that a plan names its covered
    // service(s) explicitly instead of needing a category breakdown.
    const total_service_count = totalOf(form.category_quotas) > 0 ? totalOf(form.category_quotas) : Math.max(form.total_service_count, 1);
    return {
      name: form.name,
      description: form.description || undefined,
      billing_cycle: form.billing_cycle,
      price: form.price,
      discounted_price: form.discounted_price ? Number(form.discounted_price) : undefined,
      vehicle_type_prices: toNumberMap(form.vehicle_type_prices),
      plan_discount_percent: Number(form.plan_discount_percent) || 0,
      service_pass_prices: nestPassPrices(form.service_pass_prices),
      included_service_ids: form.included_service_ids,
      category_quotas,
      total_service_count,
      vehicle_types: form.vehicle_types,
      upgrade_to_plan_ids: form.upgrade_to_plan_ids,
      is_popular: form.is_popular,
    };
  };

  const createMutation = useMutation({
    mutationFn: () => adminSubscriptionPlanApi.create(buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const updateMutation = useMutation({
    mutationFn: () => adminSubscriptionPlanApi.update(editing!.id, buildPayload()),
    onSuccess: () => {
      invalidate();
      closeModal();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const toggleActiveMutation = useMutation({
    mutationFn: (p: SubscriptionPlan) => adminSubscriptionPlanApi.update(p.id, { is_active: !p.is_active }),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminSubscriptionPlanApi.remove(id),
    onSuccess: invalidate,
  });

  const categoryName = (id: string) => categories?.find((c) => c.id === id)?.name || id;
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;
  const serviceName = (id: string) => services.find((s) => s.id === id)?.name || id;

  const toggleIncludedService = (id: string) => {
    setForm((f) => ({ ...f, included_service_ids: f.included_service_ids.includes(id) ? f.included_service_ids.filter((v) => v !== id) : [...f.included_service_ids, id] }));
  };
  const toggleVehicleType = (id: string) => {
    setForm((f) => ({ ...f, vehicle_types: f.vehicle_types.includes(id) ? f.vehicle_types.filter((v) => v !== id) : [...f.vehicle_types, id] }));
  };
  const toggleUpgradeTarget = (id: string) => {
    setForm((f) => ({
      ...f,
      upgrade_to_plan_ids: f.upgrade_to_plan_ids.includes(id) ? f.upgrade_to_plan_ids.filter((v) => v !== id) : [...f.upgrade_to_plan_ids, id],
    }));
  };

  const openEdit = (plan: SubscriptionPlan) => {
    setEditing(plan);
    const quotas: Record<string, string> = {};
    for (const [k, v] of Object.entries(plan.category_quotas || {})) quotas[k] = String(v);
    const typePrices: Record<string, string> = {};
    for (const [k, v] of Object.entries(plan.vehicle_type_prices || {})) typePrices[k] = String(v);
    setForm({
      name: plan.name,
      description: "",
      billing_cycle: plan.billing_cycle,
      price: plan.price,
      discounted_price: plan.discounted_price != null ? String(plan.discounted_price) : "",
      vehicle_type_prices: typePrices,
      service_pass_prices: flattenPassPrices(plan.service_pass_prices),
      plan_discount_percent: plan.plan_discount_percent != null ? String(plan.plan_discount_percent) : "",
      included_service_ids: plan.included_service_ids || [],
      total_service_count: plan.total_service_count || 1,
      category_quotas: quotas,
      vehicle_types: plan.vehicle_types || [],
      upgrade_to_plan_ids: plan.upgrade_to_plan_ids || [],
      is_popular: plan.is_popular,
    });
    setOpen(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Subscription plans</h1>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            Define what each plan actually covers — e.g. 4 normal washes + 1 deep clean + 1 foaming per month.
          </p>
        </div>
        <Button onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> Add plan
        </Button>
      </div>

      <DataTable<SubscriptionPlan>
        isLoading={isLoading}
        data={data || []}
        emptyTitle="No subscription plans yet"
        columns={[
          { header: "Name", accessor: (p) => p.name },
          { header: "Cycle", accessor: (p) => <span className="capitalize">{p.billing_cycle}</span> },
          { header: "Price", accessor: (p) => <span className="font-mono-num">₹{p.discounted_price ?? p.price}</span> },
          {
            header: "Covers",
            accessor: (p) =>
              p.category_quotas && Object.keys(p.category_quotas).length ? (
                <span className="text-xs">
                  {Object.entries(p.category_quotas)
                    .map(([cat, count]) => `${count}× ${categoryName(cat)}`)
                    .join(", ")}
                </span>
              ) : p.included_service_ids?.length ? (
                <span className="text-xs">
                  {p.total_service_count}× {p.included_service_ids.map(serviceName).join(", ")}
                </span>
              ) : (
                <span className="text-xs text-[var(--color-error)]">No service picked</span>
              ),
          },
          {
            header: "Vehicle types",
            accessor: (p) => (p.vehicle_types?.length ? <span className="text-xs">{p.vehicle_types.map(vehicleTypeName).join(", ")}</span> : "All"),
          },
          {
            header: "Status",
            accessor: (p) => (
              <div className="flex gap-1.5">
                <Badge tone={p.is_active ? "success" : "neutral"}>{p.is_active ? "Active" : "Inactive"}</Badge>
                {p.is_popular && <Badge tone="primary">Popular</Badge>}
              </div>
            ),
          },
          {
            header: "",
            accessor: (p) => (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(p)}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button size="sm" variant="ghost" isLoading={toggleActiveMutation.isPending} onClick={() => toggleActiveMutation.mutate(p)}>
                  <Power className={`h-3.5 w-3.5 ${p.is_active ? "" : "opacity-40"}`} />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  isLoading={deleteMutation.isPending}
                  onClick={async () => {
                    if (await confirm({ title: `Delete "${p.name}"?`, message: "This cannot be undone.", tone: "danger" })) deleteMutation.mutate(p.id);
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-[var(--color-error)]" />
                </Button>
              </div>
            ),
          },
        ]}
      />

      <Modal open={open} onClose={closeModal} title={editing ? "Edit subscription plan" : "Add subscription plan"} maxWidth="max-w-xl">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setError("");
            editing ? updateMutation.mutate() : createMutation.mutate();
          }}
        >
          <Input label="Plan name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Input label="Description (optional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Select
            label="Billing cycle"
            value={form.billing_cycle}
            onChange={(e) => setForm({ ...form, billing_cycle: e.target.value as "monthly" | "quarterly" | "yearly" })}
          >
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
            <option value="yearly">Yearly</option>
          </Select>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Price (₹)" type="number" step={20} value={form.price} onChange={(e) => setForm({ ...form, price: Number(e.target.value) })} required />
            <Input
              label="Discounted price (optional)"
              type="number"
              step={20}
              value={form.discounted_price}
              onChange={(e) => setForm({ ...form, discounted_price: e.target.value })}
            />
          </div>

          {!!vehicleTypes?.length && (
            <div className="rounded-xl border border-dashed border-gray-300 p-4">
              <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Per-vehicle-type pricing (optional)</p>
              <p className="mb-3 text-xs text-[var(--color-text-secondary)]">
                This same plan can cost different amounts by vehicle type — e.g. ₹399 for a hatchback, ₹449 for a sedan. Leave
                blank to charge the flat price above for that type.
              </p>
              <div className="space-y-2">
                {vehicleTypes.map((t) => (
                  <div key={t.id} className="grid grid-cols-[1fr_120px] items-center gap-3">
                    <span className="text-sm text-[var(--color-text-primary)]">{t.name}</span>
                    <Input
                      type="number"
                      step={20}
                      placeholder={`₹${form.price || 0}`}
                      value={form.vehicle_type_prices[t.id] || ""}
                      onChange={(e) => setForm({ ...form, vehicle_type_prices: { ...form.vehicle_type_prices, [t.id]: e.target.value } })}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-xl border-2 border-dashed border-[var(--color-primary)]/40 p-4">
            <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Which washes this pass is sold for</p>
            <p className="mb-3 text-xs text-[var(--color-text-secondary)]">
              The buyer picks ONE of these at purchase, and that is the only wash the pass ever covers.
            </p>
            <div className="flex flex-wrap gap-2">
              {services.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  onClick={() => toggleIncludedService(s.id)}
                  className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                    form.included_service_ids.includes(s.id) ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {s.name}
                </button>
              ))}
            </div>
            {!form.included_service_ids.length && <p className="mt-2 text-xs text-[var(--color-error)]">Pick at least one service.</p>}

            {/* Monthly price per wash type x vehicle type. Blank = work it
                out from the service price; a number here is the whole
                monthly price and is what the customer is charged. */}
            {!!form.included_service_ids.length && (
              <div className="mt-4 border-t border-gray-100 pt-3">
                <p className="text-sm font-medium text-[var(--color-text-primary)]">Monthly price per wash</p>
                <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
                  Leave blank to calculate it ({form.total_service_count} washes less the discount below). Steps of ₹20 — type any figure.
                </p>
                <div className="space-y-3">
                  {form.included_service_ids.map((sid) => {
                    const service = services.find((x) => x.id === sid);
                    const types = form.vehicle_types.length ? form.vehicle_types : (vehicleTypes || []).map((t) => t.id);
                    return (
                      <div key={sid}>
                        <p className="mb-1 text-xs font-semibold text-black">{service?.name || sid}</p>
                        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                          {types.map((tid) => (
                            <Input
                              key={tid}
                              label={vehicleTypeName(tid)}
                              type="number"
                              min={0}
                              step={20}
                              placeholder="auto"
                              value={form.service_pass_prices[passKey(sid, tid)] || ""}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  service_pass_prices: { ...form.service_pass_prices, [passKey(sid, tid)]: e.target.value },
                                })
                              }
                            />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-3 max-w-[220px]">
                  <Input
                    label="Pass discount (%)"
                    type="number"
                    min={0}
                    max={90}
                    hint="Only used where no price is set above"
                    value={form.plan_discount_percent}
                    onChange={(e) => setForm({ ...form, plan_discount_percent: e.target.value })}
                  />
                </div>
              </div>
            )}
            <div className="mt-3">
              <Input
                label="Total visits included"
                type="number"
                min={1}
                value={form.total_service_count}
                onChange={(e) => setForm({ ...form, total_service_count: Math.max(Number(e.target.value) || 1, 1) })}
                hint="How many times the customer can redeem this plan, in total, over its whole cycle."
              />
            </div>
          </div>

          <div className="rounded-xl border border-dashed border-gray-300 p-4">
            <p className="mb-1 text-sm font-medium text-[var(--color-text-primary)]">Advanced: split by category instead (optional)</p>
            <p className="mb-3 text-xs text-[var(--color-text-secondary)]">
              For a plan that mixes categories (e.g. 4 Normal Clean + 1 Deep Clean) rather than one fixed service — set counts
              here and they override "Total visits included" above. Leave every category at 0 to use the simple total instead.
            </p>
            <div className="space-y-2">
              {(categories || []).map((c) => (
                <div key={c.id} className="grid grid-cols-[1fr_100px] items-center gap-3">
                  <span className="text-sm text-[var(--color-text-primary)]">{c.name}</span>
                  <Input
                    type="number"
                    min={0}
                    placeholder="0"
                    value={form.category_quotas[c.id] || ""}
                    onChange={(e) => setForm({ ...form, category_quotas: { ...form.category_quotas, [c.id]: e.target.value } })}
                  />
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs font-medium text-[var(--color-text-secondary)]">Total services included: {totalOf(form.category_quotas)}</p>
          </div>

          <div>
            <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Eligible vehicle types</p>
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

          {(data || []).filter((p) => p.id !== editing?.id).length > 0 && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Can upgrade to</p>
              <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
                A subscriber on this plan can only upgrade to plans picked here — nothing is allowed by default.
              </p>
              <div className="flex flex-wrap gap-2">
                {(data || [])
                  .filter((p) => p.id !== editing?.id)
                  .map((p) => (
                    <button
                      type="button"
                      key={p.id}
                      onClick={() => toggleUpgradeTarget(p.id)}
                      className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                        form.upgrade_to_plan_ids.includes(p.id) ? "bg-[var(--color-secondary)] text-white" : "bg-gray-100 text-gray-600"
                      }`}
                    >
                      {p.name}
                    </button>
                  ))}
              </div>
            </div>
          )}

          <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            <input type="checkbox" checked={form.is_popular} onChange={(e) => setForm({ ...form, is_popular: e.target.checked })} />
            Mark as "Most popular"
          </label>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button
            type="submit"
            className="w-full"
            disabled={!form.included_service_ids.length}
            isLoading={createMutation.isPending || updateMutation.isPending}
          >
            {editing ? "Save changes" : "Add plan"}
          </Button>
        </form>
      </Modal>
    </div>
  );
}
