import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gift } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { vehicleApi } from "../../api/profile";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Button, Card, EmptyState, Modal, PageLoader, Select, StatusBadge } from "../../components/ui";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { SubscriptionPlan } from "../../types";

// Vehicle-type overrides win when set (admin can price the same plan
// differently by type — see AdminSubscriptionPlansPage), otherwise the flat
// price/discounted_price applies to every vehicle. Same pattern as
// NewBookingPage's priceFor for services.
function planPriceFor(plan: SubscriptionPlan, vehicleType: string | undefined): number {
  if (vehicleType && plan.vehicle_type_prices?.[vehicleType] != null) return plan.vehicle_type_prices[vehicleType];
  return plan.discounted_price ?? plan.price;
}

export default function SubscriptionsPage() {
  const queryClient = useQueryClient();
  const { data: plans, isLoading: plansLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: mySubs, isLoading: subsLoading } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData } = useQuery({ queryKey: ["services-for-subscriptions"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = servicesData?.data || [];
  const serviceName = (id: string) => services.find((s) => s.id === id)?.name || id;

  const [subscribingPlan, setSubscribingPlan] = useState<SubscriptionPlan | null>(null);
  const [pickedVehicleId, setPickedVehicleId] = useState("");
  const [subscribeError, setSubscribeError] = useState("");

  const [upgradingSub, setUpgradingSub] = useState<{ id: string; planId: string } | null>(null);
  const [upgradeError, setUpgradeError] = useState("");

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

  const subscribeMutation = useMutation({
    mutationFn: () => subscriptionApi.subscribe({ plan_id: subscribingPlan!.id, vehicle_id: pickedVehicleId }),
    onSuccess: () => {
      invalidateSubs();
      setSubscribingPlan(null);
      setPickedVehicleId("");
      setSubscribeError("");
    },
    onError: (err) => setSubscribeError(getErrorMessage(err)),
  });

  const cancelMutation = useMutation({
    mutationFn: subscriptionApi.cancel,
    onSuccess: invalidateSubs,
  });

  const upgradeMutation = useMutation({
    mutationFn: (newPlanId: string) => subscriptionApi.upgrade(upgradingSub!.id, newPlanId),
    onSuccess: () => {
      invalidateSubs();
      setUpgradingSub(null);
      setUpgradeError("");
    },
    onError: (err) => setUpgradeError(getErrorMessage(err)),
  });

  const vehicleName = (id: string) => {
    const v = vehicles?.find((v) => v.id === id);
    return v ? `${v.brand} ${v.model} · ${v.registration_number}` : "—";
  };

  // Which of the customer's vehicles this plan can actually be bought for —
  // matches the backend's own check in UserSubscriptionService._create_subscription.
  const eligibleVehiclesFor = (plan: SubscriptionPlan) =>
    (vehicles || []).filter((v) => !plan.vehicle_types?.length || plan.vehicle_types.includes(v.vehicle_type));

  const openSubscribe = (plan: SubscriptionPlan) => {
    setSubscribingPlan(plan);
    setSubscribeError("");
    const eligible = eligibleVehiclesFor(plan);
    setPickedVehicleId(eligible.find((v) => v.is_default)?.id || eligible[0]?.id || "");
  };

  const upgradeTargets = upgradingSub ? (plans || []).filter((p) => (plans?.find((cp) => cp.id === upgradingSub.planId)?.upgrade_to_plan_ids || []).includes(p.id)) : [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Subscriptions</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage your active plans or subscribe to a new one.</p>
      </div>

      <div>
        <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">My subscriptions</h2>
        {subsLoading ? (
          <PageLoader />
        ) : !mySubs?.length ? (
          <EmptyState icon={Gift} title="No active subscriptions" description="Subscribe to a plan below to save on regular services." />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {mySubs.map((sub) => {
              const plan = plans?.find((p) => p.id === sub.plan_id);
              const isActive = sub.effective_status === "active";
              const canUpgrade = isActive && !!plan?.upgrade_to_plan_ids?.length;
              return (
                <Card key={sub.id} className="p-5">
                  <div className="flex items-start justify-between">
                    <h3 className="font-semibold text-[var(--color-text-primary)]">{plan?.name || "Subscription"}</h3>
                    <StatusBadge status={sub.effective_status} />
                  </div>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">{vehicleName(sub.vehicle_id)}</p>
                  <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                    {sub.remaining_service_count} of {sub.total_service_count} services remaining
                  </p>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    Valid until {format(sub.end_date)}
                  </p>
                  {isActive && (
                    <div className="mt-4 flex gap-2">
                      {canUpgrade && (
                        <Button size="sm" variant="outline" onClick={() => setUpgradingSub({ id: sub.id, planId: sub.plan_id })}>
                          Upgrade
                        </Button>
                      )}
                      <Button size="sm" variant="ghost" onClick={() => cancelMutation.mutate(sub.id)}>
                        Cancel plan
                      </Button>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">Available plans</h2>
        {plansLoading ? (
          <PageLoader />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {(plans || []).map((plan) => (
              <Card key={plan.id} className="p-5">
                <h3 className="font-semibold text-[var(--color-text-primary)]">{plan.name}</h3>
                {plan.vehicle_type_prices && Object.keys(plan.vehicle_type_prices).length ? (
                  <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">
                    from ₹{Math.min(...Object.values(plan.vehicle_type_prices))}
                  </p>
                ) : (
                  <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">₹{plan.discounted_price ?? plan.price}</p>
                )}
                <p className="text-xs capitalize text-[var(--color-text-secondary)]">{plan.billing_cycle} · {plan.total_service_count} visits</p>
                {!!plan.included_service_ids?.length && (
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Covers: {plan.included_service_ids.map(serviceName).join(", ")}</p>
                )}
                {!!plan.vehicle_types?.length && (
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    For: {plan.vehicle_types.map((id) => vehicleTypes?.find((t) => t.id === id)?.name || id).join(", ")}
                  </p>
                )}
                <Button className="mt-4 w-full" onClick={() => openSubscribe(plan)}>
                  Subscribe
                </Button>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Modal open={!!subscribingPlan} onClose={() => setSubscribingPlan(null)} title={`Subscribe — ${subscribingPlan?.name || ""}`}>
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            A subscription is tied to one specific vehicle for its whole duration — pick which one this plan is for.
          </p>
          {subscribingPlan && eligibleVehiclesFor(subscribingPlan).length === 0 ? (
            <p className="text-sm text-[var(--color-error)]">
              None of your vehicles match this plan's eligible types. Add a matching vehicle first.
            </p>
          ) : (
            <>
              <Select label="Vehicle" value={pickedVehicleId} onChange={(e) => setPickedVehicleId(e.target.value)}>
                {subscribingPlan &&
                  eligibleVehiclesFor(subscribingPlan).map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.brand} {v.model} · {v.registration_number}
                    </option>
                  ))}
              </Select>
              {subscribingPlan && pickedVehicleId && (
                <p className="text-sm text-[var(--color-text-primary)]">
                  Price for this vehicle:{" "}
                  <span className="font-mono-num font-bold">
                    ₹{planPriceFor(subscribingPlan, vehicles?.find((v) => v.id === pickedVehicleId)?.vehicle_type)}
                  </span>
                </p>
              )}
            </>
          )}
          {subscribeError && <p className="text-sm text-[var(--color-error)]">{subscribeError}</p>}
          <Button className="w-full" disabled={!pickedVehicleId} isLoading={subscribeMutation.isPending} onClick={() => subscribeMutation.mutate()}>
            Confirm subscription
          </Button>
        </div>
      </Modal>

      <Modal open={!!upgradingSub} onClose={() => setUpgradingSub(null)} title="Upgrade subscription">
        <div className="space-y-3">
          {upgradeTargets.length === 0 ? (
            <p className="text-sm text-[var(--color-text-secondary)]">No upgrade is available from your current plan.</p>
          ) : (
            upgradeTargets.map((p) => (
              <Card key={p.id} className="flex items-center justify-between p-4">
                <div>
                  <p className="font-medium text-[var(--color-text-primary)]">{p.name}</p>
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    ₹{p.discounted_price ?? p.price} · {p.total_service_count} services
                  </p>
                </div>
                <Button size="sm" isLoading={upgradeMutation.isPending} onClick={() => upgradeMutation.mutate(p.id)}>
                  Upgrade
                </Button>
              </Card>
            ))
          )}
          {upgradeError && <p className="text-sm text-[var(--color-error)]">{upgradeError}</p>}
        </div>
      </Modal>
    </div>
  );
}
