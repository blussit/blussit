import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Gift } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { vehicleApi } from "../../api/profile";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Button, Card, EmptyState, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { PhoneVerificationModal } from "../../components/shared/PhoneVerificationModal";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import type { SubscriptionPlan } from "../../types";

export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [verifyOpen, setVerifyOpen] = useState(false);
  const { data: plans, isLoading: plansLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: mySubs, isLoading: subsLoading } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const { data: servicesData } = useQuery({ queryKey: ["services-for-subscriptions"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = servicesData?.data || [];
  const serviceName = (id: string) => services.find((s) => s.id === id)?.name || id;
  const typeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const [subscribingPlan, setSubscribingPlan] = useState<SubscriptionPlan | null>(null);
  const [subscribeError, setSubscribeError] = useState("");

  const [upgradingSub, setUpgradingSub] = useState<{ id: string; planId: string } | null>(null);
  const [upgradeError, setUpgradeError] = useState("");

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

  const subscribeMutation = useMutation({
    mutationFn: () => subscriptionApi.subscribe({ plan_id: subscribingPlan!.id }),
    onSuccess: (sub) => {
      invalidateSubs();
      const planName = sub.plan_name || subscribingPlan?.name;
      setSubscribingPlan(null);
      setSubscribeError("");
      // Same opaque, single-purpose token approach as a booking — see
      // PurchaseConfirmationModel / ThankYouPage.
      navigate(`/thank-you?token=${sub.confirmation_token}${planName ? `&plan=${encodeURIComponent(planName)}` : ""}`);
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

  // Which of the customer's owned vehicles this plan currently covers —
  // informational only now (subscribing no longer requires picking one),
  // shown so the customer can see what they can actually use it on.
  const eligibleVehiclesFor = (plan: SubscriptionPlan) =>
    (vehicles || []).filter((v) => !plan.vehicle_types?.length || plan.vehicle_types.includes(v.vehicle_type));

  const openSubscribe = (plan: SubscriptionPlan) => {
    setSubscribingPlan(plan);
    setSubscribeError("");
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
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    {plan?.vehicle_types?.length ? `Covers: ${plan.vehicle_types.map(typeName).join(", ")}` : "Covers: any vehicle type"}
                  </p>
                  {plan && (
                    <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                      {eligibleVehiclesFor(plan).length
                        ? `Usable on: ${eligibleVehiclesFor(plan).map((v) => `${v.brand} ${v.model} (${v.registration_number})`).join(", ")}`
                        : "You don't currently own a matching vehicle — add one to use this plan."}
                    </p>
                  )}
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
            This plan covers{" "}
            {subscribingPlan?.vehicle_types?.length ? subscribingPlan.vehicle_types.map(typeName).join(", ") : "any vehicle type"} — use it
            on any matching vehicle you own now or add later, no need to pick one now.
          </p>
          {subscribingPlan && !eligibleVehiclesFor(subscribingPlan).length && (
            <p className="text-sm text-[var(--color-warning)]">
              You don't currently own a matching vehicle — you can still subscribe and use it once you add one.
            </p>
          )}
          {subscribeError && <p className="text-sm text-[var(--color-error)]">{subscribeError}</p>}
          <Button
            className="w-full"
            isLoading={subscribeMutation.isPending}
            onClick={() => {
              if (!user?.phone_verified) {
                setVerifyOpen(true);
                return;
              }
              subscribeMutation.mutate();
            }}
          >
            Confirm subscription
          </Button>
        </div>
      </Modal>

      <PhoneVerificationModal open={verifyOpen} onClose={() => setVerifyOpen(false)} onVerified={() => { setVerifyOpen(false); subscribeMutation.mutate(); }} />

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
