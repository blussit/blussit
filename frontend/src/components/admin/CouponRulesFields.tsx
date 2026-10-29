import { useQuery } from "@tanstack/react-query";
import { adminServiceCenterApi } from "../../api/admin";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Switch } from "../ui";
import { TimePicker } from "../ui/TimePicker";
import { cn } from "../../lib/cn";

export type CouponUse = "services" | "plans" | "society" | "custom";

export const USE_OPTIONS: { key: CouponUse; label: string; hint: string }[] = [
  { key: "services", label: "Bookings", hint: "Normal service bookings (website, app, WhatsApp, manager)" },
  { key: "plans", label: "Subscription Plans", hint: "Standard plans bought or sold by a manager" },
  { key: "society", label: "Society Plans", hint: "Society enrolments and renewals" },
  { key: "custom", label: "Custom Plans", hint: "Not available yet — custom plan carts take a manager discount, not a coupon" },
];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The rule fields of a coupon / bundle as the admin edits them. */
export interface RulesForm {
  applies_to: CouponUse[];
  service_ids: string[];
  vehicle_types: string[];
  center_ids: string[];
  customerRule: "any" | "first" | "new";
  phones: string;
  weekdays: number[];
  window_on: boolean;
  window_start: string;
  window_end: string;
  stackable: boolean;
  combine: boolean;
}

/** What the API stores / returns for the rules. */
export interface RulesRecord {
  applies_to?: CouponUse[];
  applicable_service_ids?: string[];
  applicable_vehicle_types?: string[];
  applicable_center_ids?: string[];
  first_booking_only?: boolean;
  new_customers_only?: boolean;
  allowed_customer_phones?: string[];
  allowed_weekdays?: number[];
  time_window_start?: string | null;
  time_window_end?: string | null;
  stackable_with_offers?: boolean;
  combine_with_plan?: boolean;
}

export const emptyRules: RulesForm = {
  applies_to: ["services"],
  service_ids: [],
  vehicle_types: [],
  center_ids: [],
  customerRule: "any",
  phones: "",
  weekdays: [],
  window_on: false,
  window_start: "09:00",
  window_end: "18:00",
  stackable: false,
  combine: false,
};

export function rulesFromRecord(r: RulesRecord): RulesForm {
  // A coupon the server hasn't migrated yet behaves as it always did.
  const legacy = r.applies_to == null;
  return {
    applies_to: legacy ? ["services", "plans", "society"] : r.applies_to!.length ? r.applies_to! : ["services"],
    service_ids: r.applicable_service_ids ?? [],
    vehicle_types: r.applicable_vehicle_types ?? [],
    center_ids: r.applicable_center_ids ?? [],
    customerRule: r.new_customers_only ? "new" : r.first_booking_only ? "first" : "any",
    phones: (r.allowed_customer_phones ?? []).join("\n"),
    weekdays: r.allowed_weekdays ?? [],
    window_on: !!(r.time_window_start && r.time_window_end),
    window_start: r.time_window_start || "09:00",
    window_end: r.time_window_end || "18:00",
    stackable: legacy ? true : !!r.stackable_with_offers,
    combine: legacy ? true : !!r.combine_with_plan,
  };
}

/** Validated API fields for the rules (`error` is a sentence for the admin). */
export function rulesToPayload(f: RulesForm): { fields?: Required<RulesRecord>; error?: string } {
  if (!f.applies_to.length) return { error: "Tick at least one place this coupon can be used." };
  if (f.window_on && f.window_start >= f.window_end) return { error: "The time window must end after it starts." };
  return {
    fields: {
      applies_to: f.applies_to,
      applicable_service_ids: f.service_ids,
      applicable_vehicle_types: f.vehicle_types,
      applicable_center_ids: f.center_ids,
      first_booking_only: f.customerRule === "first",
      new_customers_only: f.customerRule === "new",
      allowed_customer_phones: f.phones.split(/[\s,;]+/).map((p) => p.trim()).filter(Boolean),
      allowed_weekdays: f.weekdays,
      time_window_start: f.window_on ? f.window_start : null,
      time_window_end: f.window_on ? f.window_end : null,
      stackable_with_offers: f.stackable,
      combine_with_plan: f.combine,
    },
  };
}

function Check({ checked, onChange, label, hint, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean }) {
  return (
    <label className={cn("flex items-start gap-2 rounded-xl border p-2.5 text-sm", disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer", checked ? "border-[#0E1A33] bg-[#FFF4CD]" : "border-gray-200 bg-white hover:border-gray-400")}>
      <input type="checkbox" className="mt-0.5 h-4 w-4 accent-black" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="block font-medium text-[var(--color-text-primary)]">{label}</span>
        {hint && <span className="block text-xs text-gray-500">{hint}</span>}
      </span>
    </label>
  );
}

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

/** "This Coupon Applies To: Bookings · Services: Star Wash · New Customers Only" */
export function rulesSummary(f: RulesForm, names: Record<string, string>): string {
  const nm = (id: string) => names[id] ?? "Selected";
  const parts = [`This Coupon Applies To: ${USE_OPTIONS.filter((o) => f.applies_to.includes(o.key)).map((o) => o.label).join(", ") || "Nothing"}`];
  if (f.service_ids.length) parts.push(`Services: ${f.service_ids.map(nm).join(", ")}`);
  if (f.vehicle_types.length) parts.push(`Car Types: ${f.vehicle_types.map(nm).join(", ")}`);
  if (f.center_ids.length) parts.push(`Centers: ${f.center_ids.map(nm).join(", ")}`);
  if (f.customerRule === "new") parts.push("New Customers Only");
  if (f.customerRule === "first") parts.push("First Booking Only");
  const phones = f.phones.split(/[\s,;]+/).filter(Boolean).length;
  if (phones) parts.push(`${phones} Specific Customer${phones === 1 ? "" : "s"}`);
  if (f.weekdays.length) parts.push(`Days: ${[...f.weekdays].sort().map((d) => DAYS[d]).join(", ")}`);
  if (f.window_on) parts.push(`Slots Starting ${f.window_start}–${f.window_end}`);
  parts.push(f.stackable ? "Stacks With First-Wash Offers" : "Not With First-Wash Offers");
  if (f.combine) parts.push("Can Be Used On Plan Visits");
  return parts.join(" · ");
}

/** Use-type and restriction fields — shared by the coupon form and the
 *  bundle form, with the live "This Coupon Applies To" sentence. */
export function CouponRulesFields({ rules, setRules }: { rules: RulesForm; setRules: (next: RulesForm) => void }) {
  const set = <K extends keyof RulesForm>(key: K, value: RulesForm[K]) => setRules({ ...rules, [key]: value });
  const services = useQuery({ queryKey: ["coupon-rules-services"], queryFn: () => catalogApi.services({ page_size: 100, active_only: true }), staleTime: 5 * 60_000 });
  const types = useQuery({ queryKey: ["coupon-rules-vehicle-types"], queryFn: () => vehicleTypeApi.list(true), staleTime: 5 * 60_000 });
  const centers = useQuery({ queryKey: ["coupon-rules-centers"], queryFn: () => adminServiceCenterApi.list({ page_size: 100, active_only: true }), staleTime: 5 * 60_000 });
  const names: Record<string, string> = {};
  for (const s of services.data?.data ?? []) names[s.id] = s.name;
  for (const t of types.data ?? []) names[t.id] = t.name;
  for (const c of centers.data?.data ?? []) names[c.id] = c.name;
  const bookingsOn = rules.applies_to.includes("services");

  return (
    <div className="space-y-4">
      <div className="space-y-2 rounded-xl border border-[var(--color-card-border)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">Where It Can Be Used</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {USE_OPTIONS.map((o) => (
            <Check key={o.key} disabled={o.key === "custom"} checked={rules.applies_to.includes(o.key) && o.key !== "custom"} onChange={() => set("applies_to", toggle(rules.applies_to, o.key))} label={o.label} hint={o.hint} />
          ))}
        </div>
      </div>

      <div className="space-y-3 rounded-xl border border-[var(--color-card-border)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">Restrictions (Optional)</p>
        <p className="text-xs text-gray-500">Leave a list empty to allow everything. Service, day and time rules apply to bookings only.</p>
        <PickList title="Applicable Services" disabled={!bookingsOn} items={(services.data?.data ?? []).map((s) => ({ id: s.id, name: s.name }))} chosen={rules.service_ids} onChange={(v) => set("service_ids", v)} />
        <PickList title="Applicable Car Types" items={(types.data ?? []).map((t) => ({ id: t.id, name: t.name }))} chosen={rules.vehicle_types} onChange={(v) => set("vehicle_types", v)} />
        <PickList title="Applicable Centers" disabled={!bookingsOn} items={(centers.data?.data ?? []).map((c) => ({ id: c.id, name: c.name }))} chosen={rules.center_ids} onChange={(v) => set("center_ids", v)} />

        <div>
          <p className="mb-1 text-xs font-medium text-gray-600">Which Customers</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Check checked={rules.customerRule === "any"} onChange={() => set("customerRule", "any")} label="Any Customer" />
            <Check checked={rules.customerRule === "first"} onChange={() => set("customerRule", "first")} label="First Booking Only" hint="No earlier booking on this phone or car" />
            <Check checked={rules.customerRule === "new"} onChange={() => set("customerRule", "new")} label="New Customers Only" hint="Never booked, no plan, no account history" />
          </div>
        </div>
        <label className="block text-xs font-medium text-gray-600">
          Specific Customers (Optional)
          <textarea
            value={rules.phones}
            onChange={(e) => set("phones", e.target.value)}
            rows={2}
            placeholder="10-digit mobile numbers, one per line or comma separated"
            className="mt-1 w-full rounded-xl border border-gray-200 p-2 text-sm font-normal text-[var(--color-text-primary)] focus:border-black focus:outline-none"
          />
        </label>

        <div>
          <p className="mb-1 text-xs font-medium text-gray-600">Service Days (Optional)</p>
          <div className="flex flex-wrap gap-2">
            {DAYS.map((d, i) => (
              <button
                key={d}
                type="button"
                disabled={!bookingsOn}
                aria-pressed={rules.weekdays.includes(i)}
                onClick={() => set("weekdays", toggle(rules.weekdays, i))}
                className={cn("rounded-lg border px-3 py-1.5 text-sm disabled:opacity-50", rules.weekdays.includes(i) ? "border-[#0E1A33] bg-[#FFF4CD] font-semibold" : "border-gray-200 bg-white")}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
        <Switch label="Limit To A Time Window" description="Only slots that start inside the window (IST)" checked={rules.window_on} onChange={(v) => set("window_on", v)} disabled={!bookingsOn} />
        {rules.window_on && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <TimePicker label="Window Starts" value={rules.window_start} onChange={(v) => set("window_start", v)} />
            <TimePicker label="Window Ends" value={rules.window_end} onChange={(v) => set("window_end", v)} />
          </div>
        )}
        <Switch label="Stack With First-Wash Offers" description="Off: a customer on a first-wash or launch price can't also use this coupon" checked={rules.stackable} onChange={(v) => set("stackable", v)} />
        <Switch label="Allow On Visits A Plan Partly Covers" description="Off: a visit where a plan pays for a car carries no coupon" checked={rules.combine} onChange={(v) => set("combine", v)} />
      </div>

      <p className="rounded-xl bg-[#FFFCF0] p-3 text-xs text-[var(--color-text-primary)] ring-1 ring-[#F3E5B5]" aria-live="polite">
        {rulesSummary(rules, names)}
      </p>
    </div>
  );
}

function PickList({ title, items, chosen, onChange, disabled }: { title: string; items: { id: string; name: string }[]; chosen: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  return (
    <div className={disabled ? "opacity-50" : undefined}>
      <p className="mb-1 text-xs font-medium text-gray-600">
        {title} {chosen.length ? `(${chosen.length} Selected)` : "(All)"}
      </p>
      <div className="flex max-h-32 flex-wrap gap-2 overflow-y-auto">
        {items.map((it) => (
          <button
            key={it.id}
            type="button"
            disabled={disabled}
            aria-pressed={chosen.includes(it.id)}
            onClick={() => onChange(toggle(chosen, it.id))}
            className={cn("rounded-lg border px-2.5 py-1 text-xs", chosen.includes(it.id) ? "border-[#0E1A33] bg-[#FFF4CD] font-semibold" : "border-gray-200 bg-white")}
          >
            {it.name}
          </button>
        ))}
        {!items.length && <span className="text-xs text-gray-400">Loading…</span>}
      </div>
    </div>
  );
}

