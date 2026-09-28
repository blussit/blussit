import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { CalendarClock, Gift, RefreshCw, RotateCcw } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { Button, Card, EmptyState, Modal, PageLoader } from "../../components/ui";
import { usePassPurchase } from "../../components/customer/usePassPurchase";
import { PassStatusBadge } from "../../components/customer/PassStatusBadge";
import { CustomPlanEnquiryModal } from "../../components/shared/CustomPlanEnquiryModal";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import { passHeadlinePrice } from "../../lib/passPricing";
import { buyAgainCandidates, passPlanName, passState } from "../../lib/passState";
import type { UserSubscription } from "../../types";
import { VehicleIcon } from "../../components/shared/VehicleIcon";

const PAST_SHOWN = 4;

/**
 * Monthly passes (founder model): a pass belongs to ONE vehicle type and
 * covers ONE service. Running passes sit on top with their actions; ended
 * ones below with "Buy again" (the same choices, prefilled). Anything that
 * doesn't fit — a fleet, a different rhythm — goes through the custom
 * enquiry at the bottom rather than dead-ending.
 */
// A quarterly pass costs ₹X per quarter, not per month — label by its cycle.
const CYCLE_PRICE: Record<string, string> = { monthly: "month", quarterly: "3 months", yearly: "year" };
const CYCLE_WASHES: Record<string, string> = { monthly: "a month", quarterly: "every 3 months", yearly: "a year" };
function washesPerCycle(plan: { total_service_count: number; billing_cycle?: string }): string {
  const n = plan.total_service_count;
  return `${n} wash${n === 1 ? "" : "es"} ${CYCLE_WASHES[plan.billing_cycle || "monthly"] ?? "a month"}`;
}

export default function SubscriptionsPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const purchase = usePassPurchase();

  const { data: plans, isLoading: plansLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: mySubs, isLoading: subsLoading } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const { data: servicesData } = useQuery({ queryKey: ["services-for-subscriptions"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const services = servicesData?.data || [];
  const serviceName = (id?: string | null) => services.find((s) => s.id === id)?.name || "";
  const typeNameOf = (id?: string | null) => (vehicleTypes || []).find((t) => t.id === id)?.name || "";

  const [upgradingSub, setUpgradingSub] = useState<{ id: string; planId: string } | null>(null);
  const [upgradeError, setUpgradeError] = useState("");
  const [enquiryOpen, setEnquiryOpen] = useState(false);
  const [showAllPast, setShowAllPast] = useState(false);

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

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

  const upgradeTargets = upgradingSub
    ? (plans || []).filter((p) => (plans?.find((cp) => cp.id === upgradingSub.planId)?.upgrade_to_plan_ids || []).includes(p.id))
    : [];

  const subs = mySubs || [];
  const running = subs.filter((s) => passState(s) !== "ended");
  const past = subs
    .filter((s) => passState(s) === "ended")
    .sort((a, b) => new Date(b.end_date).getTime() - new Date(a.end_date).getTime());
  const rebuyable = new Set(buyAgainCandidates(subs).map((s) => s.id));

  const coversLine = (sub: UserSubscription) => [typeNameOf(sub.vehicle_type), serviceName(sub.service_id)].filter(Boolean).join(" · ");

  const buyAgain = (sub: UserSubscription) => {
    const plan = plans?.find((p) => p.id === sub.plan_id);
    if (!plan) return;
    purchase.start(plan, { vehicleType: sub.vehicle_type, serviceId: sub.service_id, autoPay: true });
  };

  const renderRunning = (sub: UserSubscription) => {
    const plan = plans?.find((p) => p.id === sub.plan_id);
    const state = passState(sub);
    const isActive = state === "active";
    const canUpgrade = isActive && !!plan?.upgrade_to_plan_ids?.length;
    const left = sub.remaining_service_count ?? 0;
    const pct = sub.total_service_count ? Math.round((left / sub.total_service_count) * 100) : 0;
    const covers = coversLine(sub);
    return (
      <Card key={sub.id} className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-black">
              <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h3 className="truncate font-display font-bold text-black">{passPlanName(sub, plans)}</h3>
              {covers && <p className="truncate text-xs text-gray-500">{covers}</p>}
            </div>
          </div>
          <PassStatusBadge sub={sub} />
        </div>

        <p className="mt-4 text-sm text-gray-600">
          <span className="font-mono-num text-lg font-bold text-black">{left}</span> of {sub.total_service_count} washes left
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div className="h-full rounded-full bg-[#E8A900]" style={{ width: `${pct}%` }} />
        </div>

        <p className="mt-2.5 flex items-center gap-1.5 text-xs text-gray-500">
          {sub.auto_renew ? <RefreshCw className="h-3.5 w-3.5" /> : <CalendarClock className="h-3.5 w-3.5" />}
          {state === "renewing"
            ? "Your next month starts once the auto-pay charge goes through."
            : state === "used_up"
              ? `All washes used · ${sub.auto_renew ? "renews" : "valid till"} ${format(sub.end_date)}`
              : `${sub.auto_renew ? "Renews" : "Valid till"} ${format(sub.end_date)}`}
        </p>

        {state !== "paused" && (
          <div className="mt-4 flex flex-wrap gap-2">
            {isActive && (
              <Button variant="info" size="sm" onClick={() => navigate(`/app/book?subscription=${sub.id}`)}>
                Book now
              </Button>
            )}
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
                      message: left > 0
                        ? `Your ${left} remaining wash${left === 1 ? "" : "es"} stay usable until ${format(sub.end_date)} — it just won't renew after that.`
                        : state === "renewing"
                          ? "It won't renew again."
                          : `It won't renew on ${format(sub.end_date)}.`,
                    })
                  )
                    autoPayOffMutation.mutate(sub.id);
                }}
              >
                Turn off auto-pay
              </Button>
            )}
            {isActive && (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  if (
                    await confirm({
                      title: "Cancel this pass?",
                      message: `${left} unused wash${left === 1 ? "" : "es"} will be lost${sub.auto_renew ? ", and auto-pay stops immediately" : ""} — this can't be undone.`,
                      tone: "danger",
                    })
                  )
                    cancelMutation.mutate(sub.id);
                }}
              >
                Cancel pass
              </Button>
            )}
          </div>
        )}
      </Card>
    );
  };

  const endedLine = (sub: UserSubscription) => {
    if (sub.effective_status === "cancelled") return "Cancelled";
    if (new Date(sub.end_date).getTime() > Date.now()) return `All ${sub.total_service_count} washes used`;
    return `Ended ${format(sub.end_date)}`;
  };

  const renderPast = (sub: UserSubscription) => {
    const covers = coversLine(sub);
    const canBuy = rebuyable.has(sub.id) && !!plans?.some((p) => p.id === sub.plan_id);
    return (
      <div key={sub.id} className="flex items-center gap-3 rounded-2xl border border-[#F3E5B5] bg-white px-4 py-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gray-100 text-gray-500">
          <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-black">{passPlanName(sub, plans)}</p>
          {covers && <p className="truncate text-xs text-gray-500">{covers}</p>}
          <p className="truncate text-xs text-gray-400">{endedLine(sub)}</p>
        </div>
        {canBuy ? (
          <Button variant="info" size="sm" className="shrink-0" onClick={() => buyAgain(sub)}>
            <RotateCcw className="h-3.5 w-3.5" /> Buy again
          </Button>
        ) : (
          <PassStatusBadge sub={sub} />
        )}
      </div>
    );
  };

  const pastShown = showAllPast ? past : past.slice(0, PAST_SHOWN);

  return (
    <div className="space-y-8">
      <h1 className="font-display text-2xl font-bold text-black">Monthly passes</h1>

      {purchase.note && (
        <Card className="flex items-start justify-between gap-3 p-4">
          <p className="text-sm text-gray-700">{purchase.note}</p>
          {!purchase.isPaying && (
            <Button size="sm" variant="ghost" onClick={purchase.clearNote}>
              Dismiss
            </Button>
          )}
        </Card>
      )}

      <div>
        <h2 className="mb-4 font-semibold text-black">My passes</h2>
        {subsLoading ? (
          <PageLoader />
        ) : !subs.length ? (
          <EmptyState icon={Gift} title="No passes yet" description="Pick one below." />
        ) : !running.length ? (
          <p className="text-sm text-gray-500">No active pass right now.</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{running.map(renderRunning)}</div>
        )}
      </div>

      {past.length > 0 && (
        <div>
          <h2 className="mb-4 font-semibold text-black">Past passes</h2>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">{pastShown.map(renderPast)}</div>
          {past.length > PAST_SHOWN && (
            <button type="button" onClick={() => setShowAllPast((v) => !v)} className="mt-3 text-sm font-medium text-gray-600 hover:text-black">
              {showAllPast ? "Show less" : `Show all ${past.length}`}
            </button>
          )}
        </div>
      )}

      <div>
        <h2 className="mb-4 font-semibold text-black">Get a pass</h2>
        {plansLoading ? (
          <PageLoader />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(plans || []).map((plan) => {
              const from = passHeadlinePrice(plan, services, vehicleTypes);
              const menuNames = (plan.included_service_ids || []).map(serviceName).filter(Boolean);
              return (
                <Card key={plan.id} className="flex flex-col p-5">
                  <h3 className="font-display font-bold text-black">{plan.name}</h3>
                  {from != null && (
                    <p className="mt-1">
                      <span className="text-xs text-gray-500">from </span>
                      <span className="font-mono-num text-2xl font-bold text-black">₹{from}</span>
                      <span className="text-xs text-gray-500"> / {CYCLE_PRICE[plan.billing_cycle] ?? "month"}</span>
                    </p>
                  )}
                  <p className="mt-1 text-xs text-gray-500">{washesPerCycle(plan)}</p>
                  {!!menuNames.length && <p className="mt-2 text-xs text-gray-600">Choose from: {menuNames.join(", ")}</p>}
                  <Button variant="info" className="mt-4 w-full" onClick={() => purchase.start(plan)}>
                    Choose pass
                  </Button>
                </Card>
              );
            })}

            {/* Anything the standard passes can't serve — a fleet, a
                different rhythm — goes to a human instead of nowhere. */}
            <div className="flex flex-col justify-between rounded-[var(--radius-card)] border border-dashed border-[#F3E5B5] p-5">
              <div>
                <h3 className="font-display font-bold text-black">Need something else?</h3>
                <p className="mt-1 text-sm text-gray-600">More cars or a fixed weekly time — we'll price it for you.</p>
              </div>
              <Button variant="outline" className="mt-4 w-full" onClick={() => setEnquiryOpen(true)}>
                Request a custom plan
              </Button>
            </div>
          </div>
        )}
      </div>

      {purchase.sheet}

      <CustomPlanEnquiryModal open={enquiryOpen} onClose={() => setEnquiryOpen(false)} defaultName={user?.full_name} defaultPhone={user?.phone} />

      <Modal open={!!upgradingSub} onClose={() => setUpgradingSub(null)} title="Upgrade pass">
        <div className="space-y-3">
          {upgradeTargets.length === 0 ? (
            <p className="text-sm text-gray-600">No upgrade is available from your current pass.</p>
          ) : (
            <>
              <p className="text-xs text-gray-500">Your washes reset to the new pass's allowance. Auto-pay, if on, stops — turn it on again when you renew.</p>
              {upgradeTargets.map((p) => (
                <Card key={p.id} className="flex items-center justify-between p-4">
                  <div>
                    <p className="font-medium text-black">{p.name}</p>
                    <p className="text-xs text-gray-500">{washesPerCycle(p)}</p>
                  </div>
                  <Button variant="info" size="sm" isLoading={upgradeMutation.isPending} onClick={() => upgradeMutation.mutate(p.id)}>
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
