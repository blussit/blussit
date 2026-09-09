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
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";
import { format } from "../../lib/date";
import { cheapestTier, planPriceFor, tierAllows } from "../../lib/planTier";
import type { SubscriptionPlan } from "../../types";

export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const confirm = useConfirm();
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
  // The TIER being purchased — which vehicle type this card will be paid
  // for. Sets the price now and caps redemption later (that type & smaller).
  const [subscribeTypeId, setSubscribeTypeId] = useState<string | null>(null);

  // Which vehicle types a plan is sold for — the plan's own list, or every
  // active type when the plan doesn't restrict.
  const candidateTypesFor = (plan: SubscriptionPlan) =>
    plan.vehicle_types?.length
      ? plan.vehicle_types
      : (vehicleTypes || []).filter((t) => t.is_active !== false).map((t) => t.id);

  const [upgradingSub, setUpgradingSub] = useState<{ id: string; planId: string } | null>(null);
  const [upgradeError, setUpgradeError] = useState("");

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

  // Founder rule: subscriptions are ONLINE-PAYMENT ONLY. This launches
  // Razorpay for the plan's tier price (amount resolved server-side) and
  // the subscription is created by the backend only AFTER the payment
  // signature verifies — closing the modal buys nothing.
  const subscribeMutation = useMutation({
    mutationFn: () =>
      payWithRazorpay(
        { purpose: "subscription", plan_id: subscribingPlan!.id, vehicle_type: subscribeTypeId || undefined },
        { name: user?.full_name, email: user?.email, contact: user?.phone }
      ),
    onSuccess: (result) => {
      invalidateSubs();
      const planName = result.subscription?.plan_name || subscribingPlan?.name;
      setSubscribingPlan(null);
      setSubscribeError("");
      // Same opaque, single-purpose token approach as a booking — see
      // PurchaseConfirmationModel / ThankYouPage.
      navigate(`/thank-you?token=${result.confirmation_token}${planName ? `&plan=${encodeURIComponent(planName)}` : ""}`);
    },
    onError: (err) => {
      if (err instanceof PaymentCancelled) return; // modal closed — nothing purchased, nothing to show
      setSubscribeError(getErrorMessage(err));
    },
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

  // Which of the customer's owned vehicles a plan covers at a given tier —
  // the plan's type list AND the tier cap (that type or cheaper) both apply.
  const eligibleVehiclesFor = (plan: SubscriptionPlan, purchasedType?: string | null) =>
    (vehicles || []).filter(
      (v) =>
        (!plan.vehicle_types?.length || plan.vehicle_types.includes(v.vehicle_type)) &&
        tierAllows(plan, purchasedType, v.vehicle_type)
    );

  const openSubscribe = (plan: SubscriptionPlan) => {
    setSubscribingPlan(plan);
    // Default the tier to the cheapest type the plan is sold for (in
    // practice: hatchback) — same default the plan card's price shows.
    setSubscribeTypeId(cheapestTier(plan, candidateTypesFor(plan)).typeId);
    setSubscribeError("");
  };

  const upgradeTargets = upgradingSub ? (plans || []).filter((p) => (plans?.find((cp) => cp.id === upgradingSub.planId)?.upgrade_to_plan_ids || []).includes(p.id)) : [];

  return (
    <div className="space-y-8">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Plans</p>
        <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">Subscriptions</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Subscribe once, save on every wash — manage your active plans below.</p>
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
                <Card key={sub.id} className="border-[#F3E5B5] p-5">
                  <div className="flex items-start justify-between">
                    <h3 className="font-display font-bold text-[var(--color-text-primary)]">{plan?.name || "Subscription"}</h3>
                    <StatusBadge status={sub.effective_status} />
                  </div>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    {sub.vehicle_type
                      ? `Bought for: ${typeName(sub.vehicle_type)} — works for that type & smaller`
                      : plan?.vehicle_types?.length
                        ? `Covers: ${plan.vehicle_types.map(typeName).join(", ")}`
                        : "Covers: any vehicle type"}
                  </p>
                  {plan && (
                    <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                      {eligibleVehiclesFor(plan, sub.vehicle_type).length
                        ? `Usable on: ${eligibleVehiclesFor(plan, sub.vehicle_type).map((v) => `${v.brand} ${v.model} (${v.registration_number})`).join(", ")}`
                        : "You don't currently own a matching vehicle — add one to use this plan."}
                    </p>
                  )}
                  <p className="mt-2 text-sm text-[var(--color-text-secondary)]">
                    <span className="font-mono-num font-bold text-black">{sub.remaining_service_count}</span> of {sub.total_service_count} services remaining
                  </p>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100">
                    <div
                      className="h-full rounded-full bg-[#E8A900]"
                      style={{ width: `${sub.total_service_count ? Math.round(((sub.remaining_service_count ?? 0) / sub.total_service_count) * 100) : 0}%` }}
                    />
                  </div>
                  <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
                    Valid until {format(sub.end_date)}
                  </p>
                  {isActive && (
                    <div className="mt-4 flex gap-2">
                      {canUpgrade && (
                        <Button size="sm" variant="outline" onClick={() => setUpgradingSub({ id: sub.id, planId: sub.plan_id })}>
                          Upgrade
                        </Button>
                      )}
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={async () => {
                          if (
                            await confirm({
                              title: "Cancel this plan?",
                              message: `${sub.remaining_service_count} unused visit(s) will be lost — this can't be undone.`,
                              tone: "danger",
                            })
                          )
                            cancelMutation.mutate(sub.id);
                        }}
                      >
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
              <Card key={plan.id} className="border-[#F3E5B5] p-5 transition-all hover:-translate-y-0.5 hover:border-[#E8A900]/50 hover:shadow-[0_14px_30px_rgba(60,40,0,0.08)]">
                <h3 className="font-display font-bold text-[var(--color-text-primary)]">{plan.name}</h3>
                {(() => {
                  // Default display = the cheapest tier's price (hatchback in
                  // practice); bigger types are priced in the purchase step.
                  const cheap = cheapestTier(plan, candidateTypesFor(plan));
                  const varies = candidateTypesFor(plan).some((id) => planPriceFor(plan, id) !== cheap.price);
                  return (
                    <>
                      <p className="mt-1 font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">₹{cheap.price}</p>
                      {cheap.typeId && varies && (
                        <p className="text-xs text-[var(--color-text-secondary)]">{typeName(cheap.typeId)} price · changes with vehicle type</p>
                      )}
                    </>
                  );
                })()}
                <p className="text-xs capitalize text-[var(--color-text-secondary)]">{plan.billing_cycle} · {plan.total_service_count} visits</p>
                {!!plan.included_service_ids?.length && (
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">Covers: {plan.included_service_ids.map(serviceName).join(", ")}</p>
                )}
                {!!plan.vehicle_types?.length && (
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    For: {plan.vehicle_types.map((id) => vehicleTypes?.find((t) => t.id === id)?.name || id).join(", ")}
                  </p>
                )}
                <Button className="mt-4 w-full !rounded-xl bg-[#E8A900] hover:bg-[#D99A00]" onClick={() => openSubscribe(plan)}>
                  Subscribe
                </Button>
              </Card>
            ))}
          </div>
        )}
      </div>

      <Modal open={!!subscribingPlan} onClose={() => setSubscribingPlan(null)} title={`Subscribe — ${subscribingPlan?.name || ""}`}>
        <div className="space-y-4">
          {subscribingPlan && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[var(--color-text-primary)]">Which vehicle type is this plan for?</p>
              <p className="mb-2 text-xs text-[var(--color-text-secondary)]">
                The plan is priced by vehicle type. You can redeem it on that type or a smaller one — never a bigger one.
              </p>
              <div className="flex flex-wrap gap-2">
                {candidateTypesFor(subscribingPlan).map((id) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setSubscribeTypeId(id)}
                    className={`rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                      subscribeTypeId === id
                        ? "border-black bg-[#FFF4CD] text-black"
                        : "border-gray-200 text-[var(--color-text-secondary)] hover:border-gray-300"
                    }`}
                  >
                    {typeName(id)} · ₹{planPriceFor(subscribingPlan, id)}
                  </button>
                ))}
              </div>
              <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
                You pay{" "}
                <span className="font-mono-num font-bold text-black">₹{planPriceFor(subscribingPlan, subscribeTypeId)}</span>
                {" "}· {subscribingPlan.total_service_count} visits / {subscribingPlan.billing_cycle}
              </p>
            </div>
          )}
          {subscribingPlan && subscribeTypeId && !eligibleVehiclesFor(subscribingPlan, subscribeTypeId).length && (
            <p className="text-sm text-[var(--color-warning)]">
              You don't currently own a matching vehicle — you can still subscribe and use it once you add one.
            </p>
          )}
          {subscribeError && <p className="text-sm text-[var(--color-error)]">{subscribeError}</p>}
          <Button
            className="w-full"
            disabled={!!subscribingPlan && candidateTypesFor(subscribingPlan).length > 0 && !subscribeTypeId}
            isLoading={subscribeMutation.isPending}
            onClick={() => {
              if (!user?.phone_verified) {
                setVerifyOpen(true);
                return;
              }
              subscribeMutation.mutate();
            }}
          >
            Pay & subscribe
          </Button>
          <p className="text-center text-[11px] text-gray-400">Online payment only — UPI, cards, netbanking via Razorpay.</p>
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
