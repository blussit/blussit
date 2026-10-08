/**
 * Society plans (docs/SOCIETY_PLANS.md): templates offered in every
 * society, plans for one society, personal plans for one customer — none
 * of them ever on the website — plus the rate card that prices "Customise".
 */
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus } from "lucide-react";
import { Badge, Button, DataTable, ErrorState, Input, Modal, Panel, Select, Switch } from "../../components/ui";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { planCovers, rupees, societyApi, societyPlanApi, type RateCard, type SocietyPlanAdmin, type SocietyPlanInput } from "../../api/society";
import { titleCase } from "../../components/public/landing/shared";

const SCOPE: Record<SocietyPlanAdmin["scope"], string> = { template: "All Societies", society: "One Society", customer: "Personal" };

export default function AdminSocietyPlansPage() {
  const queryClient = useQueryClient();
  const plans = useQuery({ queryKey: ["society-plans"], queryFn: societyPlanApi.list });
  const card = useQuery({ queryKey: ["society-rate-card"], queryFn: societyPlanApi.rateCard });
  const [editing, setEditing] = useState<SocietyPlanAdmin | null>(null);
  const [creating, setCreating] = useState(false);
  const [showAuto, setShowAuto] = useState(false);
  const rows = (plans.data || []).filter((p) => showAuto || !p.auto_created);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold text-black">Society Plans</h1>
          <p className="text-sm text-gray-500">Per car, per month. Never shown on the website.</p>
        </div>
        <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New Plan</Button>
      </div>

      <DataTable<SocietyPlanAdmin>
        data={rows}
        isLoading={plans.isLoading}
        error={plans.error}
        onRetry={() => void plans.refetch()}
        emptyTitle="No Society Plans Yet"
        emptyDescription="Create the first template, e.g. Daily wash + 2 Star Wash at ₹1649 (MRP ₹2000)."
        onRowClick={(p) => setEditing(p)}
        columns={[
          { header: "Plan", accessor: (p) => <div><p className="font-semibold text-black">{titleCase(p.name)}</p>{planCovers(p) ? <p className="text-xs text-gray-500">{planCovers(p)}</p> : null}{p.vehicle_types.length > 0 && card.data ? <p className="text-xs text-gray-400">{p.vehicle_types.map((id) => titleCase(card.data!.vehicle_types.find((t) => t.id === id)?.name)).filter(Boolean).join(", ")}</p> : null}</div> },
          { header: "Price", accessor: (p) => <span>{rupees(p.flat_price)}{p.flat_mrp > p.flat_price && <span className="ml-1 text-xs text-gray-400 line-through">{rupees(p.flat_mrp)}</span>}{Object.keys(p.vehicle_type_prices).length > 0 && (
            <span className="block text-xs text-gray-500">
              {Object.entries(p.vehicle_type_prices).map(([id, price]) => `${titleCase(card.data?.vehicle_types.find((t) => t.id === id)?.name) || "Car"} ${rupees(price)}`).join(" · ")}
            </span>
          )}</span> },
          { header: "Who", accessor: (p) => <span>{SCOPE[p.scope]}{p.scope === "society" && p.society_names.length ? <span className="block text-xs text-gray-500">{p.society_names.join(", ")}</span> : null}{p.scope === "customer" ? <span className="block text-xs text-gray-500">{p.customer_name} · {p.customer_phone}</span> : null}{p.auto_created ? <span className="block text-xs text-gray-400">Customised By A Resident</span> : null}</span> },
          { header: "Active Cars", accessor: (p) => p.active_cars },
          { header: "Status", accessor: (p) => <Badge tone={p.is_active ? "success" : "neutral"}>{p.is_active ? "On Sale" : "Off"}</Badge> },
          { header: "", accessor: () => <Pencil className="h-4 w-4 text-gray-400" /> },
        ]}
      />
      <label className="flex items-center gap-2 text-sm text-gray-600">
        <input type="checkbox" checked={showAuto} onChange={(e) => setShowAuto(e.target.checked)} /> Show Combinations Residents Customised
      </label>

      {card.isError && !card.data && <ErrorState message="Couldn't load the rate card." onRetry={() => void card.refetch()} busy={card.isFetching} />}
      {card.data && <RateCardPanel card={card.data} onSaved={() => queryClient.invalidateQueries({ queryKey: ["society-rate-card"] })} />}

      <PlanModal open={creating || !!editing} plan={editing} card={card.data}
        cardError={card.isError && !card.data ? <ErrorState message="Couldn't load premium washes and car types." onRetry={() => void card.refetch()} busy={card.isFetching} className="p-4" /> : null}
        onClose={() => { setCreating(false); setEditing(null); }}
        onSaved={() => { setCreating(false); setEditing(null); queryClient.invalidateQueries({ queryKey: ["society-plans"] }); }} />
    </div>
  );
}

function PlanModal({ open, plan, card, cardError, onClose, onSaved }: { open: boolean; plan: SocietyPlanAdmin | null; card?: RateCard; cardError?: ReactNode; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const societies = useQuery({ queryKey: ["societies", "", ""], queryFn: () => societyApi.list(), enabled: open });
  const [form, setForm] = useState<SocietyPlanInput>(empty(card));
  const [typePrices, setTypePrices] = useState<Record<string, string>>({});
  const [typeMrps, setTypeMrps] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    setError("");
    if (plan) {
      setForm({
        name: plan.name, description: plan.description || "", bucket_days: plan.bucket_days, premium_service_id: plan.premium_service_id,
        premium_count: plan.premium_count, price: plan.flat_price, mrp: plan.flat_mrp, scope: plan.scope, society_ids: plan.society_ids,
        customer_phone: plan.customer_phone || "", is_active: plan.is_active, display_order: plan.display_order,
      });
      setTypePrices(Object.fromEntries(Object.entries(plan.vehicle_type_prices).map(([k, v]) => [k, String(v)])));
      setTypeMrps(Object.fromEntries(Object.entries(plan.vehicle_type_mrps).map(([k, v]) => [k, String(v)])));
    } else {
      setForm(empty(card));
      setTypePrices({});
      setTypeMrps({});
    }
  }, [open, plan, card]);
  const set = (p: Partial<SocietyPlanInput>) => setForm((f) => ({ ...f, ...p }));
  const nums = (m: Record<string, string>) => Object.fromEntries(Object.entries(m).filter(([, v]) => Number(v) > 0).map(([k, v]) => [k, Math.round(Number(v))]));

  const save = useMutation({
    mutationFn: () => {
      const payload = { ...form, price: Math.round(Number(form.price)), mrp: form.mrp ? Math.round(Number(form.mrp)) : undefined, vehicle_type_prices: nums(typePrices), vehicle_type_mrps: nums(typeMrps) };
      if (plan) {
        const { name, description, price, mrp, vehicle_type_prices, vehicle_type_mrps, society_ids, is_active, display_order } = payload;
        return societyPlanApi.update(plan.id, { name, description, price, mrp, vehicle_type_prices, vehicle_type_mrps, society_ids, is_active, display_order });
      }
      return societyPlanApi.create(payload);
    },
    onSuccess: () => { toast.push({ tone: "success", title: plan ? "Plan saved" : "Plan created" }); onSaved(); },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const locked = !!plan; // the combination is fixed once residents can hold it
  return (
    <Modal open={open} onClose={onClose} title={plan ? "Edit Society Plan" : "New Society Plan"} maxWidth="max-w-2xl">
      <div className="space-y-3">
        {cardError}
        <Input label="Name" value={form.name} onChange={(e) => set({ name: e.target.value })} placeholder="Daily wash + 2 Star Wash" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Select label="Bucket Wash Days" value={String(form.bucket_days)} disabled={locked} onChange={(e) => set({ bucket_days: Number(e.target.value) })}>
            {[10, 15, 20, 25, ...(card?.bucket_day_options || [])].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b).map((d) => <option key={d} value={String(d)}>{d >= 25 ? `${d} (Daily)` : d}</option>)}
          </Select>
          <Select label="Premium Wash" value={form.premium_service_id} disabled={locked} onChange={(e) => set({ premium_service_id: e.target.value })}>
            {(card?.premium_services || []).map((s) => <option key={s.id} value={s.id}>{titleCase(s.name)}</option>)}
          </Select>
          <Select label="Premium Per Month" value={String(form.premium_count)} disabled={locked} onChange={(e) => set({ premium_count: Number(e.target.value) })}>
            {[1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={String(n)}>{n}</option>)}
          </Select>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Input label="Price Per Car (₹)" type="number" min={1} value={String(form.price || "")} onChange={(e) => set({ price: Number(e.target.value) })} />
          <Input label="MRP (₹, Shown Struck)" type="number" min={1} value={String(form.mrp || "")} onChange={(e) => set({ mrp: Number(e.target.value) })} />
        </div>
        {card && card.vehicle_types.length > 0 && (
          <div>
            <p className="mb-1 text-sm font-medium">Different Price For A Car Type? <span className="font-normal text-gray-500">(Optional)</span></p>
            <div className="grid gap-2">
              {card.vehicle_types.map((t) => (
                <div key={t.id} className="grid grid-cols-[1fr_1fr_1fr] items-center gap-2">
                  <span className="text-sm">{titleCase(t.name)}</span>
                  <Input aria-label={`${t.name} price`} type="number" placeholder="Price" value={typePrices[t.id] || ""} onChange={(e) => setTypePrices({ ...typePrices, [t.id]: e.target.value })} />
                  <Input aria-label={`${t.name} MRP`} type="number" placeholder="MRP" value={typeMrps[t.id] || ""} onChange={(e) => setTypeMrps({ ...typeMrps, [t.id]: e.target.value })} />
                </div>
              ))}
            </div>
          </div>
        )}
        <Select label="Who Can Buy It" value={form.scope} disabled={locked} onChange={(e) => set({ scope: e.target.value as SocietyPlanInput["scope"] })}>
          <option value="template">Any Society (Template)</option>
          <option value="society">One Society Only</option>
          <option value="customer">One Customer Only (Personal)</option>
        </Select>
        {form.scope !== "template" && (
          <Select label={form.scope === "customer" ? "Society (Optional)" : "Society"} value={form.society_ids?.[0] || ""} onChange={(e) => set({ society_ids: e.target.value ? [e.target.value] : [] })}>
            <option value="">{form.scope === "customer" ? "Any Society" : "Pick A Society"}</option>
            {(societies.data?.rows || []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
        )}
        {form.scope !== "template" && societies.isError && !societies.data && (
          <ErrorState message="Couldn't load societies." onRetry={() => void societies.refetch()} busy={societies.isFetching} className="p-4" />
        )}
        {form.scope === "customer" && (
          <Input label="Customer Mobile" value={form.customer_phone || ""} disabled={locked} onChange={(e) => set({ customer_phone: e.target.value })} inputMode="tel" />
        )}
        <Input label="Short Description (Optional)" value={form.description || ""} onChange={(e) => set({ description: e.target.value })} />
        <Switch checked={form.is_active !== false} onChange={(v) => set({ is_active: v })} label="On Sale" description="Off = no new residents; existing ones keep it." />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button isLoading={save.isPending} onClick={() => { if (!form.name.trim() || !form.price || !form.premium_service_id) setError("Name, premium wash and price are required."); else save.mutate(); }}>
            {plan ? "Save" : "Create Plan"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function empty(card?: RateCard): SocietyPlanInput {
  return { name: "", bucket_days: 25, premium_service_id: card?.premium_services[0]?.id || "", premium_count: 2, price: 0, mrp: undefined, scope: "template", society_ids: [], is_active: true, display_order: 0 };
}

function RateCardPanel({ card, onSaved }: { card: RateCard; onSaved: () => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState(card);
  const [daysText, setDaysText] = useState(card.bucket_day_options.join(", "));
  const [countsText, setCountsText] = useState(card.premium_count_options.join(", "));
  useEffect(() => {
    setDraft(card);
    setDaysText(card.bucket_day_options.join(", "));
    setCountsText(card.premium_count_options.join(", "));
  }, [card]);
  const setRate = (key: "bucket_day_price" | "bucket_day_mrp", typeId: string | null, value: string) =>
    setDraft((d) => {
      const table = { ...d[key], by_type: { ...d[key].by_type } };
      if (typeId === null) table.default = Number(value) || 0;
      else if (value === "") delete table.by_type[typeId];
      else table.by_type[typeId] = Number(value);
      return { ...d, [key]: table };
    });
  const save = useMutation({
    mutationFn: () => {
      const { bucket_day_price, bucket_day_mrp, premium_discount_percent, premium_service_ids, allow_customise } = draft;
      return societyPlanApi.saveRateCard({
        bucket_day_price, bucket_day_mrp, premium_discount_percent, premium_service_ids, allow_customise,
        bucket_day_options: parse(daysText), premium_count_options: parse(countsText),
      });
    },
    onSuccess: () => { toast.push({ tone: "success", title: "Rate card saved" }); onSaved(); },
    onError: (err) => toast.push({ tone: "error", title: "Couldn't save", message: getErrorMessage(err) }),
  });
  const parse = (s: string) => s.split(/[,\s]+/).map(Number).filter((n) => Number.isFinite(n) && n > 0);
  return (
    <Panel title="Customise Rate Card" description="Prices a resident's own combination: days × bucket rate + premium washes at the service price (less the discount)."
      actions={<Button size="sm" isLoading={save.isPending} onClick={() => save.mutate()}>Save Rate Card</Button>}>
      <div className="space-y-4">
        <Switch checked={draft.allow_customise} onChange={(v) => setDraft({ ...draft, allow_customise: v })} label="Residents Can Customise" />
        <div className="grid gap-3 sm:grid-cols-3">
          <Input label="Bucket Wash ₹/Day" type="number" value={String(draft.bucket_day_price.default)} onChange={(e) => setRate("bucket_day_price", null, e.target.value)} />
          <Input label="Bucket Wash MRP ₹/Day" type="number" value={String(draft.bucket_day_mrp.default)} onChange={(e) => setRate("bucket_day_mrp", null, e.target.value)} />
          <Input label="Premium Discount %" type="number" value={String(draft.premium_discount_percent)} onChange={(e) => setDraft({ ...draft, premium_discount_percent: Number(e.target.value) || 0 })} />
        </div>
        <div className="grid gap-2">
          <p className="text-sm font-medium">Per Car Type ₹/Day <span className="font-normal text-gray-500">(Blank = Default)</span></p>
          {draft.vehicle_types.map((t) => (
            <div key={t.id} className="grid grid-cols-3 items-center gap-2">
              <span className="text-sm">{titleCase(t.name)}</span>
              <Input aria-label={`${t.name} rate`} type="number" placeholder="₹/day" value={draft.bucket_day_price.by_type[t.id] != null ? String(draft.bucket_day_price.by_type[t.id]) : ""} onChange={(e) => setRate("bucket_day_price", t.id, e.target.value)} />
              <Input aria-label={`${t.name} MRP rate`} type="number" placeholder="MRP ₹/day" value={draft.bucket_day_mrp.by_type[t.id] != null ? String(draft.bucket_day_mrp.by_type[t.id]) : ""} onChange={(e) => setRate("bucket_day_mrp", t.id, e.target.value)} />
            </div>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Input label="Bucket-Day Choices" value={daysText} onChange={(e) => setDaysText(e.target.value)} hint="e.g. 10, 15, 20, 25 (25 = daily)" />
          <Input label="Premium-Count Choices" value={countsText} onChange={(e) => setCountsText(e.target.value)} hint="e.g. 1, 2, 4" />
        </div>
      </div>
    </Panel>
  );
}
