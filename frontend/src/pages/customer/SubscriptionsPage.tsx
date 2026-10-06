import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Building2, CalendarClock, Gift, RefreshCw, RotateCcw, Sun } from "lucide-react";
import { subscriptionApi } from "../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { vehicleApi } from "../../api/profile";
import { Badge, Button, Card, EmptyState, Modal, PageLoader } from "../../components/ui";
import { usePassPurchase } from "../../components/customer/usePassPurchase";
import { PassStatusBadge } from "../../components/customer/PassStatusBadge";
import { CustomPlanEnquiryModal } from "../../components/shared/CustomPlanEnquiryModal";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";
import { passHeadlinePrice } from "../../lib/passPricing";
import { buyAgainCandidates, isSocietyPass, passPlanName, passState, societyPassPath } from "../../lib/passState";
import { NextPremiumWash } from "../../components/society/schedule/ResidentScheduleCard";
import type { UserSubscription } from "../../types";
import { VehicleIcon } from "../../components/shared/VehicleIcon";
import { titleCase } from "../../components/public/landing/shared";

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
  const serviceName = (id?: string | null) => titleCase(services.find((s) => s.id === id)?.name);
  const typeNameOf = (id?: string | null) => titleCase((vehicleTypes || []).find((t) => t.id === id)?.name);
  const hasSociety = (mySubs || []).some(isSocietyPass);
  const { data: myVehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list, enabled: hasSociety });
  const plateOf = (id?: string | null) => (myVehicles || []).find((v) => v.id === id)?.registration_number || "";

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
  // Society cars are shown together, one card per society (they share one
  // plan, one page and one manager); every other pass keeps its own card.
  const runningPasses = running.filter((s) => !isSocietyPass(s));
  const societyGroups = Array.from(
    running.filter(isSocietyPass).reduce((m, s) => m.set(s.society_id as string, [...(m.get(s.society_id as string) || []), s]), new Map<string, UserSubscription[]>()).values(),
  );
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

  /** One card per society: its cars, premium washes left, next premium
   *  wash, and the way in (the Society tab). Daily washes simply run. */
  const renderSocietyGroup = (cars: UserSubscription[]) => {
    const first = cars[0];
    const path = cars.map(societyPassPath).find(Boolean) || null;
    const paused = cars.every((c) => passState(c) === "paused");
    const till = cars.map((c) => c.end_date).sort().slice(-1)[0];
    return (
      <Card key={`society-${first.society_id}`} className="p-5" data-testid="society-plan-card">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#E8F0FE] text-[#0A66F0]">
              <Building2 className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h3 className="font-display font-bold leading-tight text-[#0E1A33]">{titleCase(first.society_name) || "Your Society"}</h3>
              <p className="mt-0.5 text-xs font-semibold text-[#0A66F0]">Society Plan · {titleCase(passPlanName(first, plans))}</p>
            </div>
          </div>
          {paused ? <Badge tone="warning">Paused</Badge> : <Badge tone="success">Active</Badge>}
        </div>

        <p className="mt-4 flex items-center gap-1.5 text-xs text-gray-600">
          <Sun className="h-3.5 w-3.5 text-[#0A66F0]" /> Daily wash every morning · valid till {format(till)}
        </p>

        <ul className="mt-3 space-y-2">
          {cars.map((c) => {
            const left = c.remaining_service_count ?? 0;
            return (
              <li key={c.id} className="rounded-xl bg-[#F7F9FC] p-3">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <span className="flex min-w-0 items-center gap-2">
                    <VehicleIcon vehicleTypeId={c.vehicle_type} className="h-4 w-4 shrink-0 text-[#5F6878]" />
                    <span className="min-w-0 text-sm font-semibold text-[#0E1A33]">
                      {[plateOf(c.vehicle_id), typeNameOf(c.vehicle_type)].filter(Boolean).join(" · ") || "Car"}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-gray-600">
                    <b className="font-mono-num text-sm text-[#0E1A33]">{left}</b> of {c.total_service_count} {serviceName(c.service_id) || "premium washes"} left
                  </span>
                </div>
                {passState(c) === "active" && <NextPremiumWash subscriptionId={c.id} className="mt-1.5" />}
              </li>
            );
          })}
        </ul>

        {path ? (
          <Button variant="info" size="sm" className="mt-4" onClick={() => navigate(path)}>
            Open My Society <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <p className="mt-4 text-xs text-gray-500">Your society manager books and renews this plan.</p>
        )}
      </Card>
    );
  };

  const renderRunning = (sub: UserSubscription) => {
    const plan = plans?.find((p) => p.id === sub.plan_id);
    const state = passState(sub);
    const isActive = state === "active";
    // A society pass is run by the society manager: no upgrade, auto-pay or
    // cancel here — it books (a day ahead) and renews on its society page.
    const society = isSocietyPass(sub);
    const societyPath = society ? societyPassPath(sub) : null;
    const canUpgrade = !society && isActive && !!plan?.upgrade_to_plan_ids?.length;
    const left = sub.remaining_service_count ?? 0;
    const pct = sub.total_service_count ? Math.round((left / sub.total_service_count) * 100) : 0;
    const covers = coversLine(sub);
    return (
      <Card key={sub.id} className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0E1A33]">
              <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <h3 className="truncate font-display font-bold text-[#0E1A33]">{titleCase(passPlanName(sub, plans))}</h3>
              {society && (
                <p className="truncate text-xs font-semibold text-[#0A66F0]">Society Plan{sub.society_name ? ` · ${sub.society_name}` : ""}</p>
              )}
              {covers && <p className="truncate text-xs text-gray-500">{covers}</p>}
            </div>
          </div>
          <PassStatusBadge sub={sub} />
        </div>

        <p className="mt-4 text-sm text-gray-600">
          <span className="font-mono-num text-lg font-bold text-[#0E1A33]">{left}</span> of {sub.total_service_count} {society ? serviceName(sub.service_id) || "premium washes" : "washes"} left
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#EEF3FA]">
          <div className="h-full rounded-full bg-[#0A66F0]" style={{ width: `${pct}%` }} />
        </div>

        <p className="mt-2.5 flex items-center gap-1.5 text-xs text-gray-500">
          {sub.auto_renew ? <RefreshCw className="h-3.5 w-3.5" /> : <CalendarClock className="h-3.5 w-3.5" />}
          {society
            ? state === "used_up"
              ? `Premium washes used · daily washes till ${format(sub.end_date)}`
              : `Valid till ${format(sub.end_date)} · book a day ahead, renew on your society page`
            : state === "renewing"
            ? "Your next month starts once the auto-pay charge goes through."
            : state === "used_up"
              ? `All washes used · ${sub.auto_renew ? "renews" : "valid till"} ${format(sub.end_date)}`
              : `${sub.auto_renew ? "Renews" : "Valid till"} ${format(sub.end_date)}`}
        </p>

        {society && isActive && <NextPremiumWash subscriptionId={sub.id} className="mt-2" />}
        {state !== "paused" && society && (
          <div className="mt-4 flex flex-wrap gap-2">
            {societyPath ? (
              <Button variant={isActive ? "info" : "outline"} size="sm" onClick={() => navigate(societyPath)}>
                {isActive ? "Book Now" : "Society Page"}
              </Button>
            ) : isActive ? (
              // Society link switched off: the pass still books here (a day ahead, checked by the server).
              <Button variant="info" size="sm" onClick={() => navigate(`/app/book?subscription=${sub.id}`)}>
                Book Now
              </Button>
            ) : (
              <p className="text-xs text-gray-500">Your society manager renews this plan.</p>
            )}
          </div>
        )}
        {state !== "paused" && !society && (
          <div className="mt-4 flex flex-wrap gap-2">
            {isActive && (
              <Button variant="info" size="sm" onClick={() => navigate(`/app/book?subscription=${sub.id}`)}>
                Book Now
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
                      title: "Turn Off Auto-Pay?",
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
                Turn Off Auto-Pay
              </Button>
            )}
            {isActive && (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  if (
                    await confirm({
                      title: "Cancel This Pass?",
                      message: `${left} unused wash${left === 1 ? "" : "es"} will be lost${sub.auto_renew ? ", and auto-pay stops immediately" : ""} — this can't be undone.`,
                      tone: "danger",
                    })
                  )
                    cancelMutation.mutate(sub.id);
                }}
              >
                Cancel Pass
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
      <div key={sub.id} className="flex items-center gap-3 rounded-2xl border border-[#E4E9F1] bg-white px-4 py-3.5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-gray-500">
          <VehicleIcon vehicleTypeId={sub.vehicle_type} className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-[#0E1A33]">{titleCase(passPlanName(sub, plans))}</p>
          {covers && <p className="truncate text-xs text-gray-500">{covers}</p>}
          <p className="truncate text-xs text-gray-400">{endedLine(sub)}</p>
        </div>
        {canBuy ? (
          <Button variant="info" size="sm" className="shrink-0" onClick={() => buyAgain(sub)}>
            <RotateCcw className="h-3.5 w-3.5" /> Buy Again
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
      <h1 className="font-display text-[22px] font-bold text-[#0E1A33] lg:text-[26px]">My Plans</h1>

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
        <h2 className="mb-4 font-semibold text-[#0E1A33]">Your Passes</h2>
        {subsLoading ? (
          <PageLoader />
        ) : !subs.length ? (
          <EmptyState icon={Gift} title="No Passes Yet" description="Pick one below." />
        ) : !running.length ? (
          <p className="text-sm text-gray-500">No active pass right now.</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {societyGroups.map(renderSocietyGroup)}
            {runningPasses.map(renderRunning)}
          </div>
        )}
      </div>

      {past.length > 0 && (
        <div>
          <h2 className="mb-4 font-semibold text-[#0E1A33]">Past Passes</h2>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">{pastShown.map(renderPast)}</div>
          {past.length > PAST_SHOWN && (
            <button type="button" onClick={() => setShowAllPast((v) => !v)} className="mt-3 text-sm font-medium text-gray-600 hover:text-[#0E1A33]">
              {showAllPast ? "Show Less" : `Show All ${past.length}`}
            </button>
          )}
        </div>
      )}

      <div>
        <h2 className="mb-4 font-semibold text-[#0E1A33]">Get A Pass</h2>
        {plansLoading ? (
          <PageLoader />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {(plans || []).map((plan) => {
              const from = passHeadlinePrice(plan, services, vehicleTypes);
              const menuNames = (plan.included_service_ids || []).map(serviceName).filter(Boolean);
              return (
                <Card key={plan.id} className="flex flex-col p-5">
                  <h3 className="font-display font-bold text-[#0E1A33]">{titleCase(plan.name)}</h3>
                  {from != null && (
                    <p className="mt-1">
                      <span className="text-xs text-gray-500">from </span>
                      <span className="font-mono-num text-2xl font-bold text-[#0E1A33]">₹{from}</span>
                      <span className="text-xs text-gray-500"> / {CYCLE_PRICE[plan.billing_cycle] ?? "month"}</span>
                    </p>
                  )}
                  <p className="mt-1 text-xs text-gray-500">{washesPerCycle(plan)}</p>
                  {!!menuNames.length && <p className="mt-2 text-xs text-gray-600">Choose from: {menuNames.join(", ")}</p>}
                  <Button variant="info" className="mt-4 w-full" onClick={() => purchase.start(plan)}>
                    Choose Pass
                  </Button>
                </Card>
              );
            })}

            {/* Anything the standard passes can't serve — a fleet, a
                different rhythm — goes to a human instead of nowhere. */}
            <div className="flex flex-col justify-between rounded-[var(--radius-card)] border border-dashed border-[#E4E9F1] p-5">
              <div>
                <h3 className="font-display font-bold text-[#0E1A33]">Need Something Else?</h3>
                <p className="mt-1 text-sm text-gray-600">More cars or a fixed weekly time — we'll price it for you.</p>
              </div>
              <Button variant="outline" className="mt-4 w-full" onClick={() => setEnquiryOpen(true)}>
                Request A Custom Plan
              </Button>
            </div>
          </div>
        )}
      </div>

      {purchase.sheet}

      <CustomPlanEnquiryModal open={enquiryOpen} onClose={() => setEnquiryOpen(false)} defaultName={user?.full_name} defaultPhone={user?.phone} />

      <Modal open={!!upgradingSub} onClose={() => setUpgradingSub(null)} title="Upgrade Pass">
        <div className="space-y-3">
          {upgradeTargets.length === 0 ? (
            <p className="text-sm text-gray-600">No upgrade is available from your current pass.</p>
          ) : (
            <>
              <p className="text-xs text-gray-500">Your washes reset to the new pass's allowance. Auto-pay, if on, stops — turn it on again when you renew.</p>
              {upgradeTargets.map((p) => (
                <Card key={p.id} className="flex items-center justify-between p-4">
                  <div>
                    <p className="font-medium text-[#0E1A33]">{titleCase(p.name)}</p>
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
