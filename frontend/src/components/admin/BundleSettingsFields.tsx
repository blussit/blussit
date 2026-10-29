import { Input, Select, Switch } from "../ui";
import { TimePicker } from "../ui/TimePicker";
import type { BundleSettings, CouponBundle } from "../../api/couponBundles";
import { CouponRulesFields, emptyRules, rulesFromRecord, rulesToPayload, type RulesForm } from "./CouponRulesFields";

/** One bundle's settings as the admin types them (strings until submit). */
export interface BundleForm {
  name: string;
  description: string;
  offer_kind: "standard" | "free_addon_with_service";
  coupon_type: "flat" | "percentage";
  value: string;
  min_order_value: string;
  max_discount_amount: string;
  eligible_service_keywords: string;
  free_addon_keywords: string;
  uses_per_coupon: string;
  usage_limit_per_user: string;
  no_expiry: boolean;
  expiry_date: string;
  expiry_time: string;
  rules: RulesForm;
}

export const emptyBundleForm: BundleForm = {
  name: "",
  description: "",
  offer_kind: "standard",
  coupon_type: "flat",
  value: "",
  min_order_value: "0",
  max_discount_amount: "",
  eligible_service_keywords: "",
  free_addon_keywords: "",
  uses_per_coupon: "1",
  usage_limit_per_user: "1",
  no_expiry: false,
  expiry_date: "",
  expiry_time: "23:59",
  rules: emptyRules,
};

export function bundleToForm(b: CouponBundle): BundleForm {
  return {
    name: b.name,
    description: b.description || "",
    offer_kind: b.offer_kind,
    coupon_type: b.coupon_type,
    value: String(b.value),
    min_order_value: String(b.min_order_value ?? 0),
    max_discount_amount: b.max_discount_amount != null ? String(b.max_discount_amount) : "",
    eligible_service_keywords: (b.eligible_service_keywords || []).join(", "),
    free_addon_keywords: (b.free_addon_keywords || []).join(", "),
    uses_per_coupon: String(b.uses_per_coupon),
    usage_limit_per_user: String(b.usage_limit_per_user),
    no_expiry: !b.valid_until,
    // The API sends the expiry in IST ("2026-11-30T23:59:00+05:30").
    expiry_date: b.valid_until ? b.valid_until.slice(0, 10) : "",
    expiry_time: b.valid_until ? b.valid_until.slice(11, 16) : "23:59",
    rules: rulesFromRecord(b),
  };
}

const words = (text: string) => text.split(",").map((x) => x.trim()).filter(Boolean);

/** Validates and converts the form; `error` is a sentence for the admin. */
export function formToSettings(f: BundleForm): { settings?: BundleSettings; error?: string } {
  const name = f.name.trim();
  if (name.length < 2) return { error: "Give the bundle a name." };
  const uses = Number(f.uses_per_coupon);
  const perUser = Number(f.usage_limit_per_user);
  if (!Number.isInteger(uses) || uses < 1) return { error: "Uses per coupon must be 1 or more." };
  if (!Number.isInteger(perUser) || perUser < 1) return { error: "Uses per customer must be 1 or more." };
  if (!f.no_expiry && !f.expiry_date) return { error: "Pick an expiry date, or choose No Expiry." };
  const standard = f.offer_kind === "standard";
  const value = Number(f.value || 0);
  if (standard && !(value > 0)) return { error: "Enter the discount value." };
  if (standard && f.coupon_type === "percentage" && value > 100) return { error: "A percentage discount can't be more than 100." };
  if (!standard && (!words(f.eligible_service_keywords).length || !words(f.free_addon_keywords).length)) {
    return { error: "Free add-on offers need both keyword lists." };
  }
  const rules = rulesToPayload(f.rules);
  if (!rules.fields) return { error: rules.error };
  return {
    settings: {
      ...rules.fields,
      name,
      description: f.description.trim() || null,
      offer_kind: f.offer_kind,
      coupon_type: f.coupon_type,
      value: standard ? value : 0,
      min_order_value: standard ? Number(f.min_order_value || 0) : 0,
      max_discount_amount: standard && f.coupon_type === "percentage" && f.max_discount_amount ? Number(f.max_discount_amount) : null,
      eligible_service_keywords: standard ? [] : words(f.eligible_service_keywords),
      free_addon_keywords: standard ? [] : words(f.free_addon_keywords),
      uses_per_coupon: uses,
      usage_limit_per_user: perUser,
      // Explicit IST offset — the date and time are what the admin sees.
      valid_until: f.no_expiry ? null : `${f.expiry_date}T${f.expiry_time || "23:59"}:00+05:30`,
    },
  };
}

/** Name, expiry, uses and the benefit — shared by the import page and the
 *  bundle's Edit dialog so both ask for exactly the same things. */
export function BundleSettingsFields({ form, setForm }: { form: BundleForm; setForm: (next: BundleForm) => void }) {
  const set = <K extends keyof BundleForm>(key: K, value: BundleForm[K]) => setForm({ ...form, [key]: value });
  const standard = form.offer_kind === "standard";
  return (
    <div className="space-y-4">
      <Input label="Bundle Name" value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={80} placeholder="Diwali Mailer 2026" required />
      <Input label="Description" value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Optional admin note" maxLength={300} />

      <div className="space-y-3 rounded-xl border border-[var(--color-card-border)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">Offer</p>
        <Select label="Offer Type" value={form.offer_kind} onChange={(e) => set("offer_kind", e.target.value as BundleForm["offer_kind"])}>
          <option value="standard">Standard Discount</option>
          <option value="free_addon_with_service">Free Add-On With Selected Service</option>
        </Select>
        {standard ? (
          <>
            <Select label="Discount Type" value={form.coupon_type} onChange={(e) => set("coupon_type", e.target.value as BundleForm["coupon_type"])}>
              <option value="flat">Flat Amount</option>
              <option value="percentage">Percentage</option>
            </Select>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Input label={form.coupon_type === "percentage" ? "Percent Off" : "Amount Off (₹)"} type="number" min={0} value={form.value} onChange={(e) => set("value", e.target.value)} required />
              <Input label="Min Order (₹)" type="number" min={0} value={form.min_order_value} onChange={(e) => set("min_order_value", e.target.value)} />
              {form.coupon_type === "percentage" && (
                <Input label="Max Discount (₹)" type="number" min={0} value={form.max_discount_amount} onChange={(e) => set("max_discount_amount", e.target.value)} placeholder="No Cap" />
              )}
            </div>
          </>
        ) : (
          <div className="space-y-3 rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3">
            <Input label="Eligible Service Keywords" value={form.eligible_service_keywords} onChange={(e) => set("eligible_service_keywords", e.target.value)} placeholder="star, deep cleaning" />
            <Input label="Free Add-On Keywords" value={form.free_addon_keywords} onChange={(e) => set("free_addon_keywords", e.target.value)} placeholder="extra bike wash" />
          </div>
        )}
      </div>

      <div className="space-y-3 rounded-xl border border-[var(--color-card-border)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">Uses</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Input
            label="Uses Per Coupon"
            type="number"
            min={1}
            value={form.uses_per_coupon}
            onChange={(e) => set("uses_per_coupon", e.target.value)}
            hint="1 = one-time code. N = each code can be used N times in total, by anyone."
          />
          <Input
            label="Uses Per Customer"
            type="number"
            min={1}
            value={form.usage_limit_per_user}
            onChange={(e) => set("usage_limit_per_user", e.target.value)}
            hint="How many times the same customer may use one code."
          />
        </div>
      </div>

      <div className="space-y-3 rounded-xl border border-[var(--color-card-border)] p-3">
        <p className="text-sm font-semibold text-[var(--color-text-primary)]">Expiry (IST)</p>
        <Switch label="No Expiry" description="Codes stay valid until used up or switched off." checked={form.no_expiry} onChange={(v) => set("no_expiry", v)} />
        {!form.no_expiry && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Expiry Date" type="date" value={form.expiry_date} onChange={(e) => set("expiry_date", e.target.value)} />
            <TimePicker label="Expiry Time" value={form.expiry_time} onChange={(v) => set("expiry_time", v)} />
          </div>
        )}
        <p className="text-xs text-gray-500">One expiry for the whole bundle. A code stops working at that exact time.</p>
      </div>

      <CouponRulesFields rules={form.rules} setRules={(rules) => set("rules", rules)} />
    </div>
  );
}
