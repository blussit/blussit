import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Banknote, Check, Copy, CreditCard, Power, RefreshCw } from "lucide-react";
import { subscriptionApi, type ManagerOfferResult } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Badge, Button, Input, Select, Spinner, Switch } from "../../components/ui";
import { VehicleIcon } from "../../components/shared/VehicleIcon";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { baseGroups } from "../../lib/serviceMix";
import { cleanMobileInput, validateIndianMobile } from "../../lib/validators";
import type { SubscriptionPlan } from "../../types";

type DiscountMode = "none" | "amount" | "coupon";

/**
 * Manager sells a plan over the phone or at the door: name + phone find or
 * create the customer, plan + vehicle type + service price it exactly like
 * the customer's own purchase sheet would (PassPurchaseSheet). Three ways
 * it can end — a one-time WhatsApp payment link (optionally discounted or
 * coupon'd), a full-rate auto-pay link, or cash collected on the spot — and
 * a small "active plans" list below to stop selling one.
 */
export default function ManagerSellPlanPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const confirm = useConfirm();

  const { data: plans } = useQuery({ queryKey: ["subscription-plans-for-sale"], queryFn: () => subscriptionApi.plans(true) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData } = useQuery({ queryKey: ["services-for-manager-offer"], queryFn: () => catalogApi.services({ page_size: 100 }) });

  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [planId, setPlanId] = useState("");
  const [vehicleType, setVehicleType] = useState<string | null>(null);
  const [serviceId, setServiceId] = useState<string | null>(null);
  const [recurring, setRecurring] = useState(false);
  const [discountMode, setDiscountMode] = useState<DiscountMode>("none");
  const [discountAmount, setDiscountAmount] = useState("");
  const [couponCode, setCouponCode] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<"link" | "cash">("link");
  const [sendWhatsApp, setSendWhatsApp] = useState(true);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ManagerOfferResult | null>(null);
  const [copied, setCopied] = useState(false);

  const plan = plans?.find((p) => p.id === planId) || null;

  // The types this plan is sold for (all of them when it doesn't restrict).
  const types = useMemo(() => {
    const all = (vehicleTypes || []).filter((t) => t.is_active !== false).sort((a, b) => a.display_order - b.display_order);
    const allowed = plan?.vehicle_types || [];
    return allowed.length ? all.filter((t) => allowed.includes(t.id)) : all;
  }, [vehicleTypes, plan]);

  // The services this pass may cover, narrowed to what's actually offered
  // for the chosen type — same collapsing logic the customer sheet uses.
  const menu = useMemo(() => {
    const all = servicesData?.data || [];
    const allowed = plan?.included_service_ids || [];
    const onMenu = allowed.length ? all.filter((s) => allowed.includes(s.id)) : all;
    if (!vehicleType) return [];
    return baseGroups(onMenu, vehicleType).map((g) => g.primary);
  }, [servicesData, plan, vehicleType]);

  // A clean form whenever a different plan is picked.
  useEffect(() => {
    setVehicleType(null);
    setServiceId(null);
    setResult(null);
  }, [planId]);
  useEffect(() => {
    if (!vehicleType && types.length) setVehicleType(types[0].id);
  }, [types, vehicleType]);
  useEffect(() => {
    if (serviceId && !menu.some((s) => s.id === serviceId)) setServiceId(null);
  }, [menu, serviceId]);
  useEffect(() => {
    if (recurring) setDiscountMode("none");
  }, [recurring]);

  const cleanPhone = validateIndianMobile(phone);

  const { data: preview, isFetching: previewing } = useQuery({
    queryKey: ["manager-offer-preview", planId, vehicleType, serviceId, cleanPhone, recurring, discountMode, discountAmount, couponCode],
    queryFn: () =>
      subscriptionApi.managerOfferPreview({
        plan_id: planId,
        vehicle_type: vehicleType!,
        service_id: serviceId!,
        customer_phone: cleanPhone || undefined,
        recurring,
        discount_amount: discountMode === "amount" ? Number(discountAmount) || 0 : 0,
        coupon_code: discountMode === "coupon" && couponCode.trim() ? couponCode.trim() : undefined,
      }),
    enabled: !!planId && !!vehicleType && !!serviceId,
    retry: false,
    staleTime: 5_000,
  });

  const canSubmit =
    name.trim().length >= 2 && !!cleanPhone && !!planId && !!vehicleType && !!serviceId && !preview?.already_has_pass &&
    (discountMode !== "coupon" || preview?.coupon_valid !== false);

  const submit = async () => {
    setError("");
    const next: Record<string, string> = {};
    if (name.trim().length < 2) next.name = "Enter the customer's name.";
    if (!cleanPhone) next.phone = "Enter a valid 10-digit mobile number.";
    if (!planId) next.plan = "Pick a plan.";
    if (!vehicleType) next.vehicleType = "Pick a vehicle type.";
    if (!serviceId) next.service = "Pick a service.";
    setFieldErrors(next);
    if (Object.keys(next).length) return;

    setSubmitting(true);
    try {
      const out = await subscriptionApi.managerOfferCreate({
        customer_name: name.trim(),
        customer_phone: cleanPhone!,
        plan_id: planId,
        vehicle_type: vehicleType!,
        service_id: serviceId!,
        recurring,
        payment_method: recurring ? "link" : paymentMethod,
        discount_amount: !recurring && discountMode === "amount" ? Number(discountAmount) || 0 : undefined,
        coupon_code: !recurring && discountMode === "coupon" && couponCode.trim() ? couponCode.trim() : undefined,
        send_whatsapp: sendWhatsApp,
      });
      setResult(out);
      queryClient.invalidateQueries({ queryKey: ["center-subscription-overview"] });
      if (out.kind === "cash") {
        pushToast({ tone: "success", title: "Plan activated", message: `₹${out.amount} collected in cash.` });
        navigate("/manager/subscribers");
      } else {
        pushToast({
          tone: "success",
          title: out.kind === "autopay" ? "Auto-pay link sent" : "Payment link sent",
          message: sendWhatsApp ? "The customer was messaged on WhatsApp." : "Share the link below with the customer.",
        });
      }
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const cancelOffer = async () => {
    if (!result?.order_id) return;
    if (!(await confirm({ title: "Cancel this offer?", message: "The link stops working. The customer can no longer pay it.", tone: "danger" }))) return;
    try {
      await subscriptionApi.managerOfferVoid(result.order_id);
      pushToast({ tone: "success", title: "Offer cancelled" });
      setResult(null);
    } catch (err) {
      pushToast({ tone: "error", title: "Couldn't cancel", message: getErrorMessage(err) });
    }
  };

  const copyLink = async () => {
    if (!result?.short_url) return;
    try {
      await navigator.clipboard.writeText(result.short_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard unavailable — the link is still visible to select by hand
    }
  };

  const startAnother = () => {
    setResult(null);
    setName("");
    setPhone("");
    setDiscountMode("none");
    setDiscountAmount("");
    setCouponCode("");
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Sell a plan</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
          A customer over the phone or at the door — send a payment link on WhatsApp, set up auto-pay, or mark it paid in cash.
        </p>
      </div>

      {result ? (
        <div className="max-w-xl rounded-2xl border border-[#F3E5B5] bg-white p-5">
          {result.kind === "cash" ? (
            <>
              <p className="flex items-center gap-2 text-sm font-semibold text-black">
                <Check className="h-4 w-4 text-[var(--color-success)]" /> Plan activated — ₹{result.amount} recorded as cash.
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-semibold text-black">
                {result.kind === "autopay" ? "Auto-pay link ready" : "Payment link ready"} — ₹{result.amount}
                {result.kind === "autopay" ? "/month" : ""}
              </p>
              <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                {sendWhatsApp ? "Already sent to the customer on WhatsApp." : "Not sent — copy it and share it yourself."}
              </p>
              <div className="mt-3 flex items-center gap-2 rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] px-3.5 py-2.5">
                <span className="min-w-0 flex-1 truncate font-mono-num text-sm text-black">{result.short_url}</span>
                <Button size="sm" variant="outline" onClick={copyLink}>
                  <Copy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
                The plan activates the moment it's paid — nothing to do here. You can cancel this offer if the customer changed their mind.
              </p>
              <Button size="sm" variant="ghost" className="mt-2" onClick={cancelOffer}>
                Cancel this offer
              </Button>
            </>
          )}
          <div className="mt-4 flex gap-2">
            <Button variant="outline" onClick={startAnother}>
              Sell another plan
            </Button>
            <Button variant="outline" onClick={() => navigate("/manager/subscribers")}>
              Go to Subscriptions
            </Button>
          </div>
        </div>
      ) : (
        <div className="max-w-xl space-y-6 rounded-2xl border border-[#F3E5B5] bg-white p-5">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Customer name" value={name} onChange={(e) => setName(e.target.value)} error={fieldErrors.name} placeholder="E.g. Rahul Sharma" />
            <Input
              label="Customer mobile"
              value={phone}
              inputMode="numeric"
              onChange={(e) => setPhone(cleanMobileInput(e.target.value))}
              error={fieldErrors.phone}
              placeholder="10-digit mobile"
            />
          </div>

          <Select label="Plan" value={planId} onChange={(e) => setPlanId(e.target.value)} error={fieldErrors.plan}>
            <option value="">Select a plan</option>
            {(plans || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>

          {plan && (
            <>
              <div>
                <p className="mb-1.5 text-sm font-medium text-black">Vehicle type</p>
                <div className="flex flex-wrap gap-2">
                  {types.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => setVehicleType(t.id)}
                      aria-pressed={vehicleType === t.id}
                      className={`flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-medium transition-colors ${
                        vehicleType === t.id ? "border-black bg-[#FFF4CD] text-black" : "border-[#E5E7EB] text-gray-700 hover:border-gray-400"
                      }`}
                    >
                      <VehicleIcon vehicleTypeId={t.id} className="h-4 w-4 text-gray-500" />
                      {t.name}
                    </button>
                  ))}
                  {!types.length && <p className="text-xs text-gray-500">This plan has no vehicle types configured.</p>}
                </div>
                {fieldErrors.vehicleType && <p className="mt-1 text-xs text-[var(--color-error)]">{fieldErrors.vehicleType}</p>}
              </div>

              <div>
                <p className="mb-1.5 text-sm font-medium text-black">Service</p>
                <div className="flex flex-wrap gap-2">
                  {menu.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => setServiceId(s.id)}
                      aria-pressed={serviceId === s.id}
                      className={`rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors ${
                        serviceId === s.id ? "border-black bg-[#FFF4CD] text-black" : "border-[#E5E7EB] text-gray-700 hover:border-gray-400"
                      }`}
                    >
                      {s.name}
                    </button>
                  ))}
                  {!menu.length && <p className="text-xs text-gray-500">{vehicleType ? "No service on this pass fits that vehicle type." : "Pick a vehicle type first."}</p>}
                </div>
                {fieldErrors.service && <p className="mt-1 text-xs text-[var(--color-error)]">{fieldErrors.service}</p>}
              </div>

              <Switch
                checked={recurring}
                onChange={setRecurring}
                label="Auto-pay"
                description="Renews itself every month at the full price — no discount or coupon. Off = a one-time link for this month only."
              />

              {!recurring && (
                <div>
                  <p className="mb-2 text-sm font-medium text-black">Give a discount?</p>
                  <div className="grid grid-cols-3 gap-2">
                    {(
                      [
                        { id: "none", label: "No discount" },
                        { id: "amount", label: "₹ off" },
                        { id: "coupon", label: "Coupon code" },
                      ] as const
                    ).map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => setDiscountMode(opt.id)}
                        className={`rounded-xl border px-3 py-2 text-xs font-semibold transition-colors ${
                          discountMode === opt.id ? "border-black bg-[#FFF4CD] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                  {discountMode === "amount" && (
                    <Input
                      className="mt-2.5"
                      label="Discount (₹)"
                      inputMode="numeric"
                      value={discountAmount}
                      onChange={(e) => setDiscountAmount(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      placeholder="0"
                    />
                  )}
                  {discountMode === "coupon" && (
                    <Input
                      className="mt-2.5"
                      label="Coupon code"
                      value={couponCode}
                      onChange={(e) => setCouponCode(e.target.value.toUpperCase())}
                      placeholder="E.g. SAVE100"
                      error={preview?.coupon_valid === false ? preview.coupon_error || "Invalid coupon code" : undefined}
                    />
                  )}
                </div>
              )}

              {!recurring && (
                <div>
                  <p className="mb-2 text-sm font-medium text-black">How will it be paid?</p>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {(
                      [
                        { id: "link", icon: CreditCard, title: "WhatsApp link", sub: "Customer pays online" },
                        { id: "cash", icon: Banknote, title: "Cash", sub: "Collected by you, right now" },
                      ] as const
                    ).map((opt) => (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => setPaymentMethod(opt.id)}
                        className={`flex items-start gap-3 rounded-xl border-2 p-3 text-left ${
                          paymentMethod === opt.id ? "border-black bg-[#FFF4CD]" : "border-gray-200 bg-white"
                        }`}
                      >
                        <opt.icon className="mt-0.5 h-4 w-4 shrink-0 text-black" />
                        <span>
                          <span className="block text-sm font-semibold text-black">{opt.title}</span>
                          <span className="block text-xs text-gray-500">{opt.sub}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="rounded-xl border border-[#F3E5B5] bg-[#FAFAFA] p-4">
                {previewing ? (
                  <p className="flex items-center gap-2 text-sm text-gray-500">
                    <Spinner className="h-4 w-4" /> Working out the price…
                  </p>
                ) : preview ? (
                  <>
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm text-gray-600">
                        {preview.visits} × {preview.service_name}
                      </span>
                      <span className="text-right">
                        {preview.discount > 0 && <span className="mr-1.5 text-xs text-gray-400 line-through">₹{preview.base_price}</span>}
                        <span className="font-mono-num text-xl font-bold text-black">₹{preview.final_price}</span>
                        {recurring && <span className="text-xs text-gray-500">/mo</span>}
                      </span>
                    </div>
                    {preview.already_has_pass && (
                      <p className="mt-2 text-sm text-[var(--color-error)]">This number already has an active pass for that vehicle type and service.</p>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-gray-500">Pick a vehicle type and a service to see the price.</p>
                )}
              </div>

              <Switch
                checked={sendWhatsApp}
                onChange={setSendWhatsApp}
                label="Tell the customer on WhatsApp"
                description={
                  recurring || paymentMethod === "link"
                    ? "Sends the link the moment it's ready. Off = you copy it and share it yourself."
                    : "Sends one message confirming the plan is active."
                }
              />

              {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

              <Button className="w-full" disabled={!canSubmit} isLoading={submitting} onClick={submit}>
                {recurring ? "Send auto-pay link" : paymentMethod === "cash" ? "Mark as paid — activate plan" : "Send payment link"}
              </Button>
            </>
          )}
        </div>
      )}

      <ActivePlansList plans={plans} />
    </div>
  );
}

/** A compact "stop selling a plan" list — the manager-safe half of the
 * admin catalogue page: is_active only, never price or contents. */
function ActivePlansList({ plans }: { plans: SubscriptionPlan[] | undefined }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const confirm = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);

  const discontinue = async (planId: string, name: string) => {
    if (!(await confirm({ title: `Stop selling "${name}"?`, message: "New purchases are refused. Customers who already hold it are unaffected.", tone: "danger" }))) return;
    setBusyId(planId);
    try {
      await subscriptionApi.discontinuePlan(planId);
      pushToast({ tone: "success", title: "Plan discontinued" });
      queryClient.invalidateQueries({ queryKey: ["subscription-plans-for-sale"] });
    } catch (err) {
      pushToast({ tone: "error", title: "Couldn't discontinue", message: getErrorMessage(err) });
    } finally {
      setBusyId(null);
    }
  };

  if (!plans?.length) return null;
  return (
    <div className="max-w-xl">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Active plans</p>
      <div className="divide-y divide-gray-100 rounded-2xl border border-[#F3E5B5] bg-white">
        {plans.map((p) => (
          <div key={p.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-black">{p.name}</p>
              <p className="text-xs text-[var(--color-text-secondary)]">₹{p.discounted_price ?? p.price} · {p.billing_cycle}</p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone="success">Active</Badge>
              <Button size="sm" variant="ghost" isLoading={busyId === p.id} onClick={() => discontinue(p.id, p.name)}>
                <Power className="h-3.5 w-3.5" /> Stop selling
              </Button>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-1.5 flex items-center gap-1 text-xs text-[var(--color-text-secondary)]">
        <RefreshCw className="h-3 w-3" /> A discontinued plan disappears from this list once you refresh.
      </p>
    </div>
  );
}
