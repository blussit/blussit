import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarClock, Gift, RefreshCw } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { vehicleApi } from "../../api/profile";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Button, Card, EmptyState, Modal, PageLoader, StatusBadge } from "../../components/ui";
import { PassPurchaseSheet } from "../../components/customer/PassPurchaseSheet";
import { CustomPlanEnquiryModal } from "../../components/shared/CustomPlanEnquiryModal";
import { PhoneVerificationModal } from "../../components/shared/PhoneVerificationModal";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { PaymentCancelled, payWithRazorpay } from "../../lib/razorpay";
import { format } from "../../lib/date";
import { passFromPrice } from "../../lib/passPricing";
import type { SubscriptionPlan, UserSubscription } from "../../types";
import { VehicleIcon } from "../../components/shared/VehicleIcon";

/**
 * Monthly passes (founder model): a pass belongs to ONE car and covers ONE
 * service. Buy as many as you have cars; a car never carries two. Anything
 * that doesn't fit — a fleet, a different rhythm — goes through the custom
 * enquiry at the bottom rather than dead-ending.
 */
export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [verifyOpen, setVerifyOpen] = useState(false);

  const { data: plans, isLoading: plansLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: mySubs, isLoading: subsLoading } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: servicesData } = useQuery({ queryKey: ["services-for-subscriptions"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const services = servicesData?.data || [];
  const serviceName = (id?: string | null) => services.find((s) => s.id === id)?.name || "";
  const plateOf = (id?: string | null) => (vehicles || []).find((v) => v.id === id)?.registration_number || "";
  const vehicleTypeOf = (id?: string | null) => (vehicles || []).find((v) => v.id === id)?.vehicle_type;

  const [buyingPlan, setBuyingPlan] = useState<SubscriptionPlan | null>(null);
  const [purchaseError, setPurchaseError] = useState("");
  const [pendingPurchase, setPendingPurchase] = useState<{ vehicleId: string; serviceId: string; autoPay: boolean } | null>(null);
  const [upgradingSub, setUpgradingSub] = useState<{ id: string; planId: string } | null>(null);
  const [upgradeError, setUpgradeError] = useState("");
  const [enquiryOpen, setEnquiryOpen] = useState(false);

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });


  const purchaseMutation = useMutation({
    mutationFn: (args: { vehicleId: string; serviceId: string; autoPay: boolean }) => {
      setPurchaseError("");
      return payWithRazorpay(
        {
          purpose: "subscription",
          plan_id: buyingPlan!.id,
          vehicle_id: args.vehicleId,
          service_id: args.serviceId,
          auto_pay: args.autoPay,
        },
        { name: user?.full_name, email: user?.email, contact: user?.phone }
      );
    },
    onSuccess: (result) => {
      invalidateSubs();
      const planName = result.subscription?.plan_name || buyingPlan?.name;
      setBuyingPlan(null);
      setPurchaseError("");
      navigate(`/thank-you?token=${result.confirmation_token}${planName ? `&plan=${encodeURIComponent(planName)}` : ""}`);
    },
    onError: (err) => {
      // A pass only exists once the payment verifies, so an abandoned
      // checkout has bought nothing — say so rather than closing silently.
      if (err instanceof PaymentCancelled) {
        setPurchaseError("Payment wasn't completed, so your pass hasn't started. Nothing was charged — you can try again.");
        return;
      }
      setPurchaseError(getErrorMessage(err));
    },
  });

  const cancelMutation = useMutation({ mutationFn: subscriptionApi.cancel, onSuccess: invalidateSubs });
  const autoPayOffMutation = useMutation({
    mutationFn: (id: string) => subscriptionApi.setAutoPay(id, false),
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

  const startPurchase = (args: { vehicleId: string; serviceId: string; autoPay: boolean }) => {
    if (!user?.phone_verified) {
      setPendingPurchase(args);
      setVerifyOpen(true);
      return;
    }
    purchaseMutation.mutate(args);
  };

  const upgradeTargets = upgradingSub
    ? (plans || []).filter((p) => (plans?.find((cp) => cp.id === upgradingSub.planId)?.upgrade_to_plan_ids || []).includes(p.id))
    : [];

  const renderPass = (sub: UserSubscription) => {
    const plan = plans?.find((p) => p.id === sub.plan_id);
    const isActive = sub.effective_status === "active";
    const canUpgrade = isActive && !!plan?.upgrade_to_plan_ids?.length;
    const pct = sub.total_service_count ? Math.round(((sub.remaining_service_count ?? 0) / sub.total_service_count) * 100) : 0;
    const plate = plateOf(sub.vehicle_id);
    const covered = serviceName(sub.service_id);
    return (
      <Card key={sub.id} className="border-[#E5E7EB] p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="font-display font-bold text-[var(--color-text-primary)]">{covered || plan?.name || "Monthly pass"}</h3>
            {plate && (
              <p className="mt-0.5 flex items-center gap-1.5 text-xs text-gray-500">
                <VehicleIcon vehicleTypeId={vehicleTypeOf(sub.vehicle_id)} className="h-3 w-3" />
                <span className="font-mono-num">{plate}</span>
              </p>
            )}
          </div>
          <StatusBadge status={sub.effective_status} />
        </div>

        <p className="mt-3 text-sm text-gray-600">
          <span className="font-mono-num font-bold text-black">{sub.remaining_service_count}</span> of {sub.total_service_count} washes left
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div className="h-full rounded-full bg-black" style={{ width: `${pct}%` }} />
        </div>

        <p className="mt-2.5 flex items-center gap-1.5 text-xs text-gray-500">
          {sub.auto_renew ? (
            <>
              <RefreshCw className="h-3.5 w-3.5" />
              Renews automatically on {format(sub.end_date)}
            </>
          ) : (
            <>
              <CalendarClock className="h-3.5 w-3.5" />
              {isActive ? `Ends ${format(sub.end_date)} — no auto-renewal` : `Ended ${format(sub.end_date)}`}
            </>
          )}
        </p>

        {isActive && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => navigate(`/app/book?subscription=${sub.id}`)} disabled={sub.remaining_service_count <= 0}>
              Book a wash
            </Button>
            {canUpgrade && (
              <Button size="sm" variant="outline" onClick={() => setUpgradingSub({ id: sub.id, planId: sub.plan_id })}>
                Upgrade
              </Button>
            )}
            {sub.auto_renew && (
              <Button
                size="sm"
                variant="ghost"
                isLoading={autoPayOffMutation.isPending && autoPayOffMutation.variables === sub.id}
                onClick={async () => {
                  if (
                    await confirm({
                      title: "Turn off auto-pay?",
                      message: `Your ${sub.remaining_service_count} remaining wash(es) stay usable until ${format(sub.end_date)} — it just won't renew after that.`,
                    })
                  )
                    autoPayOffMutation.mutate(sub.id);
                }}
              >
                Turn off auto-pay
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={async () => {
                if (
                  await confirm({
                    title: "Cancel this pass?",
                    message: `${sub.remaining_service_count} unused wash(es) will be lost${sub.auto_renew ? ", and auto-pay stops immediately" : ""} — this can't be undone.`,
                    tone: "danger",
                  })
                )
                  cancelMutation.mutate(sub.id);
              }}
            >
              Cancel pass
            </Button>
          </div>
        )}
      </Card>
    );
  };

  return (
    <div className="space-y-8">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-black">Passes</p>
        <h1 className="mt-1 font-display text-2xl font-bold text-[var(--color-text-primary)]">Monthly passes</h1>
        <p className="mt-1 text-sm text-gray-600">One car, one service, one monthly price — book a wash whenever you need it.</p>
      </div>

      <div>
        <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">My passes</h2>
        {subsLoading ? (
          <PageLoader />
        ) : !mySubs?.length ? (
          <EmptyState icon={Gift} title="No passes yet" description="Pick a pass below to save on every wash for one of your cars." />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{mySubs.map(renderPass)}</div>
        )}
      </div>

      <div>
        <h2 className="mb-4 font-semibold text-[var(--color-text-primary)]">Get a pass</h2>
        {plansLoading ? (
          <PageLoader />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(plans || []).map((plan) => {
              const from = passFromPrice(plan, services, vehicleTypes);
              const menuNames = (plan.included_service_ids || []).map(serviceName).filter(Boolean);
              return (
                <Card key={plan.id} className="flex flex-col border-[#E5E7EB] p-5">
                  <h3 className="font-display font-bold text-[var(--color-text-primary)]">{plan.name}</h3>
                  {from != null && (
                    <p className="mt-1">
                      <span className="text-xs text-gray-500">from </span>
                      <span className="font-mono-num text-2xl font-bold text-[var(--color-text-primary)]">₹{from}</span>
                      <span className="text-xs text-gray-500"> / month</span>
                    </p>
                  )}
                  <p className="mt-1 text-xs text-gray-500">
                    {plan.total_service_count} washes a month · final price depends on your car and service
                  </p>
                  {!!menuNames.length && (
                    <p className="mt-2 text-xs text-gray-600">Choose from: {menuNames.join(", ")}</p>
                  )}
                  <Button className="mt-4 w-full" onClick={() => { setBuyingPlan(plan); setPurchaseError(""); }}>
                    Choose this pass
                  </Button>
                </Card>
              );
            })}

            {/* Anything the standard passes can't serve — a fleet, a
                different rhythm — goes to a human instead of nowhere. */}
            <Card className="flex flex-col justify-between border-dashed border-gray-300 p-5">
              <div>
                <h3 className="font-display font-bold text-[var(--color-text-primary)]">Something else?</h3>
                <p className="mt-1 text-sm text-gray-600">
                  More cars, more washes, or a fixed time every week — tell us what you need and we'll price it for you.
                </p>
              </div>
              <Button variant="outline" className="mt-4 w-full" onClick={() => setEnquiryOpen(true)}>
                Request a custom plan
              </Button>
            </Card>
          </div>
        )}
      </div>

      <PassPurchaseSheet
        plan={buyingPlan}
        open={!!buyingPlan}
        onClose={() => setBuyingPlan(null)}
        onConfirm={startPurchase}
        isPaying={purchaseMutation.isPending}
        error={purchaseError}
      />

      <PhoneVerificationModal
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
        onVerified={() => {
          setVerifyOpen(false);
          if (pendingPurchase) purchaseMutation.mutate(pendingPurchase);
          setPendingPurchase(null);
        }}
      />

      <CustomPlanEnquiryModal open={enquiryOpen} onClose={() => setEnquiryOpen(false)} defaultName={user?.full_name} defaultPhone={user?.phone} />

      <Modal open={!!upgradingSub} onClose={() => setUpgradingSub(null)} title="Upgrade pass">
        <div className="space-y-3">
          {upgradeTargets.length === 0 ? (
            <p className="text-sm text-gray-600">No upgrade is available from your current pass.</p>
          ) : (
            <>
              <p className="text-xs text-gray-500">
                Your washes reset to the new pass's allowance for the rest of this month. If auto-pay is on it stops with the old
                price — turn it back on by buying the new pass when this month ends.
              </p>
              {upgradeTargets.map((p) => (
                <Card key={p.id} className="flex items-center justify-between p-4">
                  <div>
                    <p className="font-medium text-[var(--color-text-primary)]">{p.name}</p>
                    <p className="text-xs text-gray-500">{p.total_service_count} washes a month</p>
                  </div>
                  <Button size="sm" isLoading={upgradeMutation.isPending} onClick={() => upgradeMutation.mutate(p.id)}>
                    Upgrade
                  </Button>
                </Card>
              ))}
            </>
          )}
          {upgradeError && <p className="text-sm text-[var(--color-error)]">{upgradeError}</p>}
        </div>
      </Modal>
    </div>
  );
}
