import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Button, Modal, Spinner } from "../ui";
import { baseGroups } from "../../lib/serviceMix";
import { getErrorMessage } from "../../lib/api-client";
import type { SubscriptionPlan } from "../../types";
import { VehicleIcon } from "../shared/VehicleIcon";
import { titleCase } from "../public/landing/shared";

/**
 * Buying a monthly pass asks exactly TWO questions (2026-09 model):
 *
 *   1. WHICH VEHICLE TYPE — hatchback, SUV, bike... No registration, no
 *      brand/model: a pass is for a type, and the server applies it to
 *      any booking of that type + service automatically.
 *   2. WHICH SERVICE — one wash from the plan's own menu.
 *
 * The type and the service decide the price, quoted by the SAME backend
 * function that will charge for it — this sheet never does its own money
 * arithmetic, so what's shown is always what's charged.
 */
/** "Buy again": the ended pass's own choices, already picked. */
export interface PassPrefill {
  vehicleType?: string | null;
  serviceId?: string | null;
  /** Default true — a repeat buyer is offered auto-pay first. */
  autoPay?: boolean;
}

export function PassPurchaseSheet({
  plan,
  open,
  onClose,
  onConfirm,
  isPaying,
  error,
  prefill,
}: {
  plan: SubscriptionPlan | null;
  open: boolean;
  onClose: () => void;
  onConfirm: (args: { vehicleType: string; serviceId: string; autoPay: boolean }) => void;
  isPaying: boolean;
  error?: string;
  prefill?: PassPrefill | null;
}) {
  const {
    data: vehicleTypes,
    isError: typesFailed,
    isFetching: typesFetching,
    refetch: refetchTypes,
  } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), enabled: open });
  const {
    data: servicesData,
    isError: servicesFailed,
    isFetching: servicesFetching,
    refetch: refetchServices,
  } = useQuery({
    queryKey: ["services-for-passes"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
    enabled: open,
  });

  const [vehicleType, setVehicleType] = useState<string | null>(null);
  const [serviceId, setServiceId] = useState<string | null>(null);
  const [autoPay, setAutoPay] = useState(true);

  // The types this plan is sold for (all of them when the plan doesn't
  // restrict), in the admin's display order.
  const types = useMemo(() => {
    const all = (vehicleTypes || []).filter((t) => t.is_active !== false).sort((a, b) => a.display_order - b.display_order);
    const allowed = plan?.vehicle_types || [];
    return allowed.length ? all.filter((t) => allowed.includes(t.id)) : all;
  }, [vehicleTypes, plan]);

  // The services this pass may cover: the plan's own menu, narrowed to what
  // is actually offered for the chosen type. baseGroups collapses variant
  // families ("Bike Wash", "Bike Wash (2 bikes)") into one choice.
  const menu = useMemo(() => {
    const all = servicesData?.data || [];
    const allowed = plan?.included_service_ids || [];
    const onMenu = allowed.length ? all.filter((s) => allowed.includes(s.id)) : all;
    if (!vehicleType) return [];
    return baseGroups(onMenu, vehicleType).map((g) => g.primary);
  }, [servicesData, plan, vehicleType]);

  // Only once the catalogue is in — an empty menu while it loads must not
  // wipe a prefilled service.
  useEffect(() => {
    if (!servicesData || !vehicleTypes) return;
    if (serviceId && !menu.some((s) => s.id === serviceId)) setServiceId(null);
  }, [menu, serviceId, servicesData, vehicleTypes]);

  // Each opening starts from the prefill ("Buy again") or a clean sheet.
  const prefillType = prefill?.vehicleType ?? null;
  const prefillService = prefill?.serviceId ?? null;
  const prefillAutoPay = prefill?.autoPay ?? true;
  useEffect(() => {
    if (!open) return;
    setVehicleType(prefillType); // null = seeded below from THIS plan's types
    setServiceId(prefillService);
    setAutoPay(prefillAutoPay);
  }, [open, plan?.id, prefillType, prefillService, prefillAutoPay]);
  useEffect(() => {
    if (!open || !types.length) return;
    // An updater, so it sees the prefill queued by the reset above in this
    // same commit; a prefilled type this plan doesn't sell falls back to
    // the first one, like a fresh sheet.
    setVehicleType((current) => (current && types.some((t) => t.id === current) ? current : types[0].id));
  }, [open, types, vehicleType]);

  // The live price — re-quoted from the server whenever the type or the
  // service changes, never computed here.
  const { data: quote, isFetching: quoting, error: quoteError, refetch: refetchQuote } = useQuery({
    queryKey: ["pass-quote", plan?.id, vehicleType, serviceId],
    queryFn: () => subscriptionApi.quotePass({ plan_id: plan!.id, vehicle_type: vehicleType!, service_id: serviceId! }),
    enabled: !!plan && !!vehicleType && !!serviceId,
    retry: false,
  });

  const chosenType = types.find((t) => t.id === vehicleType) || null;
  const blocked = quote?.vehicle_has_pass;
  const canPay = !!vehicleType && !!serviceId && !!quote && !blocked && !quoting;

  return (
    <Modal open={open} onClose={onClose} title={plan ? `Monthly Pass — ${titleCase(plan.name)}` : "Monthly Pass"}>
      <div className="space-y-6">
        {/* ---- 1. Which vehicle type ---- */}
        <div>
          <p className="text-sm font-semibold text-[#0E1A33]">1. Which Vehicle Is This Pass For?</p>
          <p className="mt-0.5 text-xs text-gray-500">Just the type — it applies to any {chosenType ? chosenType.name.toLowerCase() : "vehicle of that type"} you book.</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {types.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setVehicleType(t.id)}
                aria-pressed={vehicleType === t.id}
                className={`flex items-center gap-2 rounded-xl border px-3.5 py-2.5 text-sm font-medium transition-colors ${
                  vehicleType === t.id ? "border-[#0A66F0] bg-[var(--color-primary-light)] text-[#0E1A33]" : "border-[#E5E7EB] text-gray-700 hover:border-gray-400"
                }`}
              >
                <VehicleIcon vehicleTypeId={t.id} className="h-4 w-4 text-gray-500" />
                {titleCase(t.name)}
                {vehicleType === t.id && <Check className="h-4 w-4 text-[#0E1A33]" />}
              </button>
            ))}
            {!types.length &&
              (typesFailed && !vehicleTypes ? (
                <p role="alert" className="text-xs text-gray-500">
                  Couldn't load vehicle types.{" "}
                  <button type="button" disabled={typesFetching} onClick={() => void refetchTypes()} className="font-semibold text-[#0A66F0] hover:underline disabled:opacity-60">
                    {typesFetching ? "Trying…" : "Try Again"}
                  </button>
                </p>
              ) : vehicleTypes ? (
                <p className="text-xs text-gray-500">This pass isn't sold for any vehicle type right now.</p>
              ) : (
                <p className="text-xs text-gray-500">Loading vehicle types…</p>
              ))}
          </div>
        </div>

        {/* ---- 2. Which service ---- */}
        <div>
          <p className="text-sm font-semibold text-[#0E1A33]">2. Which Service Should It Cover?</p>
          <p className="mt-0.5 text-xs text-gray-500">Every visit on this pass is this wash. Add-ons can still be added per booking.</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {menu.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setServiceId(s.id)}
                aria-pressed={serviceId === s.id}
                className={`rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors ${
                  serviceId === s.id ? "border-[#0A66F0] bg-[var(--color-primary-light)] text-[#0E1A33]" : "border-[#E5E7EB] text-gray-700 hover:border-gray-400"
                }`}
              >
                {titleCase(s.name)}
              </button>
            ))}
            {!menu.length &&
              (servicesFailed && !servicesData ? (
                <p role="alert" className="text-xs text-gray-500">
                  Couldn't load the services.{" "}
                  <button type="button" disabled={servicesFetching} onClick={() => void refetchServices()} className="font-semibold text-[#0A66F0] hover:underline disabled:opacity-60">
                    {servicesFetching ? "Trying…" : "Try Again"}
                  </button>
                </p>
              ) : chosenType && !servicesData ? (
                <p className="text-xs text-gray-500">Loading services…</p>
              ) : (
                <p className="text-xs text-gray-500">
                  {chosenType ? `No service on this pass is offered for a ${chosenType.name}. Try another type, or request a custom plan.` : "Pick a vehicle type first."}
                </p>
              ))}
          </div>
        </div>

        {/* ---- the price, straight from the server ---- */}
        <div className="rounded-xl border border-[#E5E7EB] bg-[#F7F9FC] p-4">
          {!vehicleType || !serviceId ? (
            <p className="text-sm text-gray-500">Pick a vehicle type and a service to see the price.</p>
          ) : quoting ? (
            <p className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner className="h-4 w-4" /> Working out your price…
            </p>
          ) : quoteError ? (
            <p className="text-sm text-[var(--color-error)]">
              {getErrorMessage(quoteError)}{" "}
              <button type="button" onClick={() => void refetchQuote()} className="font-semibold text-[#0A66F0] hover:underline">
                Try Again
              </button>
            </p>
          ) : quote ? (
            <>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-gray-600">
                  {chosenType ? `${titleCase(chosenType.name)} · ` : ""}
                  {quote.visits} × {titleCase(quote.service_name)}
                </span>
                <span className="font-mono-num text-xl font-bold text-[#0E1A33]">₹{quote.price}</span>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                ₹{quote.price_per_wash} per wash
                {quote.discount_percent > 0 ? ` · ${quote.discount_percent}% off vs booking them one by one` : ""} · per month
              </p>
              {blocked && (
                <p className="mt-2 text-sm text-[var(--color-error)]">You already have an active pass for this vehicle type and service. Use it up first, or pick another.</p>
              )}
            </>
          ) : null}
        </div>

        {/* ---- renewal ---- */}
        <div>
          <p className="mb-2 text-sm font-semibold text-[#0E1A33]">How Should It Renew?</p>
          <div className="flex gap-2">
            {[
              { value: true, label: "Auto-Pay On", hint: "Renews every month · cancel anytime" },
              { value: false, label: "Just This Month", hint: "Ends when the washes run out" },
            ].map((option) => (
              <button
                key={String(option.value)}
                type="button"
                onClick={() => setAutoPay(option.value)}
                aria-pressed={autoPay === option.value}
                className={`flex-1 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors ${
                  autoPay === option.value ? "border-[#0A66F0] bg-[var(--color-primary-light)] font-semibold text-[#0E1A33]" : "border-[#E5E7EB] text-gray-600 hover:border-gray-400"
                }`}
              >
                {option.label}
                <span className="mt-0.5 block text-[11px] font-normal text-gray-500">{option.hint}</span>
              </button>
            ))}
          </div>
        </div>

        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

        <Button variant="info" className="w-full font-semibold" disabled={!canPay} isLoading={isPaying} onClick={() => onConfirm({ vehicleType: vehicleType!, serviceId: serviceId!, autoPay })}>
          {quote ? `Pay ₹${quote.price} & Activate` : "Pay & Activate"}
        </Button>
        <p className="text-center text-[11px] text-gray-500">
          Online payment only — UPI, cards or netbanking. The pass starts as soon as the payment goes through.
        </p>
      </div>
    </Modal>
  );
}
