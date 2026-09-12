import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { vehicleApi } from "../../api/profile";
import { Button, Input, Modal, Select, Spinner } from "../ui";
import { baseGroups } from "../../lib/serviceMix";
import { getErrorMessage } from "../../lib/api-client";
import type { SubscriptionPlan, Vehicle } from "../../types";
import { VehicleIcon } from "../shared/VehicleIcon";

/**
 * Buying a monthly pass asks exactly TWO questions (founder model):
 *
 *   1. WHICH CAR — an existing vehicle, or a new one added right here with
 *      the same fields the garage page uses.
 *   2. WHICH SERVICE — one wash from the plan's own menu (Jet Wash,
 *      Waterless, Star Wash; Deep Cleaning is deliberately not sold as a
 *      pass).
 *
 * Everything else follows: the car decides the vehicle type, the type and
 * the service decide the price, and the price is quoted by the SAME backend
 * function that will charge for it — this sheet never does its own money
 * arithmetic, so what's shown is always what's charged.
 */
export function PassPurchaseSheet({
  plan,
  open,
  onClose,
  onConfirm,
  isPaying,
  error,
}: {
  plan: SubscriptionPlan | null;
  open: boolean;
  onClose: () => void;
  onConfirm: (args: { vehicleId: string; serviceId: string; autoPay: boolean }) => void;
  isPaying: boolean;
  error?: string;
}) {
  const queryClient = useQueryClient();
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, enabled: open });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list(), enabled: open });
  const { data: servicesData } = useQuery({
    queryKey: ["services-for-passes"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
    enabled: open,
  });

  const [vehicleId, setVehicleId] = useState<string | null>(null);
  const [serviceId, setServiceId] = useState<string | null>(null);
  const [autoPay, setAutoPay] = useState(true);
  const [addingCar, setAddingCar] = useState(false);
  const [newPlate, setNewPlate] = useState("");
  const [newBrand, setNewBrand] = useState("");
  const [newTypeId, setNewTypeId] = useState("");
  const [formError, setFormError] = useState("");

  const chosenVehicle = (vehicles || []).find((v) => v.id === vehicleId) || null;

  // The services this pass may cover: the plan's own menu, narrowed to what
  // the CHOSEN CAR can actually have done to it. Offering "Bike Wash" for a
  // Thar is the kind of thing a customer notices immediately and trusts you
  // less for — and the backend refuses it anyway.
  //
  // baseGroups also collapses variant families, so "Bike Wash" and "Bike
  // Wash (2 bikes)" are ONE choice rather than two chips competing for the
  // same tap. (Quantity on a pass is a booking-time decision, not a
  // purchase-time one.)
  const menu = useMemo(() => {
    const all = servicesData?.data || [];
    const allowed = plan?.included_service_ids || [];
    const onMenu = allowed.length ? all.filter((s) => allowed.includes(s.id)) : all;
    if (!chosenVehicle) return [];
    return baseGroups(onMenu, chosenVehicle.vehicle_type).map((g) => g.primary);
  }, [servicesData, plan, chosenVehicle]);

  // Switching car can invalidate the chosen service (car service -> bike).
  useEffect(() => {
    if (serviceId && !menu.some((s) => s.id === serviceId)) setServiceId(null);
  }, [menu, serviceId]);

  // A clean sheet each time it opens.
  useEffect(() => {
    if (!open) return;
    setServiceId(null);
    setAutoPay(true);
    setFormError("");
    setAddingCar(false);
    setNewPlate("");
    setNewBrand("");
  }, [open, plan?.id]);

  // Default to the car they use most.
  useEffect(() => {
    if (!open || vehicleId || !vehicles?.length) return;
    setVehicleId((vehicles.find((v) => v.is_default) || vehicles[0]).id);
  }, [open, vehicles, vehicleId]);

  useEffect(() => {
    if (!newTypeId && vehicleTypes?.length) setNewTypeId(vehicleTypes[0].id);
  }, [vehicleTypes, newTypeId]);

  // The live price. Re-quoted from the server whenever the car or the
  // service changes — never computed here.
  const { data: quote, isFetching: quoting, error: quoteError } = useQuery({
    queryKey: ["pass-quote", plan?.id, vehicleId, serviceId],
    queryFn: () => subscriptionApi.quotePass({ plan_id: plan!.id, vehicle_id: vehicleId!, service_id: serviceId! }),
    enabled: !!plan && !!vehicleId && !!serviceId,
    retry: false,
  });

  const addCarMutation = useMutation({
    mutationFn: () =>
      vehicleApi.create({
        vehicle_type: newTypeId,
        brand: newBrand.trim().split(" ")[0] || "Vehicle",
        model: newBrand.trim().split(" ").slice(1).join(" ") || newBrand.trim() || "—",
        registration_number: newPlate.trim().toUpperCase(),
        is_default: !vehicles?.length,
      }),
    onSuccess: (created: Vehicle) => {
      queryClient.invalidateQueries({ queryKey: ["vehicles"] });
      setVehicleId(created.id);
      setAddingCar(false);
      setNewPlate("");
      setNewBrand("");
      setFormError("");
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });

  const blocked = quote?.vehicle_has_pass;
  const canPay = !!vehicleId && !!serviceId && !!quote && !blocked && !quoting;

  return (
    <Modal open={open} onClose={onClose} title={plan ? `Monthly pass — ${plan.name}` : "Monthly pass"}>
      <div className="space-y-6">
        {/* ---- 1. Which car ---- */}
        <div>
          <p className="text-sm font-semibold text-black">1. Which car is this pass for?</p>
          <p className="mt-0.5 text-xs text-gray-500">A pass belongs to one car — one car, one pass.</p>

          {!addingCar ? (
            <div className="mt-2.5 space-y-2">
              {(vehicles || []).map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setVehicleId(v.id)}
                  aria-pressed={vehicleId === v.id}
                  className={`flex w-full items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition-colors ${
                    vehicleId === v.id ? "border-black bg-[#FAFAFA]" : "border-[#E5E7EB] hover:border-gray-400"
                  }`}
                >
                  <VehicleIcon vehicleTypeId={v.vehicle_type} className="h-4 w-4 shrink-0 text-gray-500" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-black">
                      {v.brand} {v.model}
                    </span>
                    <span className="font-mono-num block text-xs text-gray-500">{v.registration_number}</span>
                  </span>
                  {vehicleId === v.id && <Check className="h-4 w-4 shrink-0 text-black" />}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setAddingCar(true)}
                className="w-full rounded-xl border border-dashed border-gray-300 px-3.5 py-2.5 text-sm font-medium text-gray-600 hover:border-gray-400"
              >
                + Add a different car
              </button>
            </div>
          ) : (
            <div className="mt-2.5 space-y-3 rounded-xl border border-dashed border-gray-300 p-3.5">
              <Input
                label="Registration number"
                placeholder="MP09AB1234"
                value={newPlate}
                onChange={(e) => setNewPlate(e.target.value.toUpperCase())}
                required
              />
              <Input label="Brand & model" placeholder="Maruti Swift" value={newBrand} onChange={(e) => setNewBrand(e.target.value)} />
              <Select label="Vehicle type" value={newTypeId} onChange={(e) => setNewTypeId(e.target.value)}>
                {(vehicleTypes || []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  disabled={newPlate.trim().length < 4 || !newTypeId}
                  isLoading={addCarMutation.isPending}
                  onClick={() => addCarMutation.mutate()}
                >
                  Save car
                </Button>
                <Button size="sm" variant="outline" onClick={() => { setAddingCar(false); setFormError(""); }}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* ---- 2. Which service ---- */}
        <div>
          <p className="text-sm font-semibold text-black">2. Which service should it cover?</p>
          <p className="mt-0.5 text-xs text-gray-500">Every visit on this pass is this wash. Add-ons can still be added per booking.</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {menu.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setServiceId(s.id)}
                aria-pressed={serviceId === s.id}
                className={`rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors ${
                  serviceId === s.id ? "border-black bg-[#FAFAFA] text-black" : "border-[#E5E7EB] text-gray-700 hover:border-gray-400"
                }`}
              >
                {s.name}
              </button>
            ))}
            {!menu.length && (
              <p className="text-xs text-gray-500">
                {chosenVehicle
                  ? `No service on this pass is offered for a ${chosenVehicle.brand} ${chosenVehicle.model}. Try another car, or request a custom plan.`
                  : "Pick a car first."}
              </p>
            )}
          </div>
        </div>

        {/* ---- the price, straight from the server ---- */}
        <div className="rounded-xl border border-[#E5E7EB] bg-[#FAFAFA] p-4">
          {!vehicleId || !serviceId ? (
            <p className="text-sm text-gray-500">Pick a car and a service to see the price.</p>
          ) : quoting ? (
            <p className="flex items-center gap-2 text-sm text-gray-500">
              <Spinner className="h-4 w-4" /> Working out your price…
            </p>
          ) : quoteError ? (
            <p className="text-sm text-[var(--color-error)]">{getErrorMessage(quoteError)}</p>
          ) : quote ? (
            <>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-gray-600">
                  {quote.visits} × {quote.service_name}
                  {chosenVehicle ? ` · ${chosenVehicle.registration_number}` : ""}
                </span>
                <span className="font-mono-num text-xl font-bold text-black">₹{quote.price}</span>
              </div>
              <p className="mt-1 text-xs text-gray-500">
                ₹{quote.price_per_wash} per wash
                {quote.discount_percent > 0 ? ` · ${quote.discount_percent}% off vs booking them one by one` : ""} · per month
              </p>
              {blocked && (
                <p className="mt-2 text-sm text-[var(--color-error)]">
                  {chosenVehicle?.registration_number || "This car"} already has an active pass. Pick another car.
                </p>
              )}
            </>
          ) : null}
        </div>

        {/* ---- renewal ---- */}
        <div>
          <p className="mb-2 text-sm font-semibold text-black">How should it renew?</p>
          <div className="flex gap-2">
            {[
              { value: true, label: "Auto-pay on", hint: "Renews every month · cancel anytime" },
              { value: false, label: "Just this month", hint: "Ends when the washes run out" },
            ].map((option) => (
              <button
                key={String(option.value)}
                type="button"
                onClick={() => setAutoPay(option.value)}
                aria-pressed={autoPay === option.value}
                className={`flex-1 rounded-xl border px-3.5 py-2.5 text-left text-sm transition-colors ${
                  autoPay === option.value ? "border-black bg-[#FAFAFA] font-semibold text-black" : "border-[#E5E7EB] text-gray-600 hover:border-gray-400"
                }`}
              >
                {option.label}
                <span className="mt-0.5 block text-[11px] font-normal text-gray-500">{option.hint}</span>
              </button>
            ))}
          </div>
        </div>

        {(formError || error) && <p className="text-sm text-[var(--color-error)]">{formError || error}</p>}

        <Button
          className="w-full"
          disabled={!canPay}
          isLoading={isPaying}
          onClick={() => onConfirm({ vehicleId: vehicleId!, serviceId: serviceId!, autoPay })}
        >
          {quote ? `Pay ₹${quote.price} & activate` : "Pay & activate"}
        </Button>
        <p className="text-center text-[11px] text-gray-500">
          Online payment only — UPI, cards or netbanking. The pass starts as soon as the payment goes through.
        </p>
      </div>
    </Modal>
  );
}
