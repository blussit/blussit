import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check } from "lucide-react";
import { subscriptionApi } from "../../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../../api/catalog";
import { useAuth } from "../../../context/AuthContext";
import type { SubscriptionPlan } from "../../../types";
import { INR, SectionHeader, SectionShell, priceView, vehicleNames } from "./shared";
import { AutoRail } from "./AutoRail";

const CYCLE_ORDER: Record<SubscriptionPlan["billing_cycle"], number> = { monthly: 0, quarterly: 1, yearly: 2 };
const CYCLE_LABEL: Record<SubscriptionPlan["billing_cycle"], string> = {
  monthly: "per month",
  quarterly: "per 3 months",
  yearly: "per year",
};

/**
 * Real subscription plans from the backend. Hidden entirely when there are
 * none (unless `showEmpty`), so the landing page never shows made-up plans.
 */
export function PlansShowcase({ id = "plans", showEmpty = false }: { id?: string; showEmpty?: boolean }) {
  const navigate = useNavigate();
  const { user } = useAuth();

  const { data: plans, isLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: servicesData } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

  const services = servicesData?.data ?? [];
  const active = (plans ?? [])
    .filter((p) => p.is_active !== false)
    .sort((a, b) => CYCLE_ORDER[a.billing_cycle] - CYCLE_ORDER[b.billing_cycle] || a.price - b.price);

  if (!isLoading && active.length === 0 && !showEmpty) return null;

  const choose = () => navigate(user?.role === "customer" ? "/app/subscriptions" : "/login");

  return (
    <SectionShell id={id} className="bg-white">
      <SectionHeader
        title="Wash plans"
        subtitle="Pay once for a set of washes and we come on schedule. Cheaper per wash than booking one at a time."
      />

      {isLoading ? (
        <AutoRail className="mt-8 sm:mt-10" gridClassName="sm:grid-cols-2 sm:gap-5 md:grid-cols-3" intervalMs={600000}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-[320px] w-[80vw] max-w-[340px] shrink-0 snap-center sm:w-auto sm:max-w-none animate-pulse rounded-2xl border border-cream-line bg-white" aria-hidden="true" />
          ))}
        </AutoRail>
      ) : active.length === 0 ? (
        <p className="mt-8 rounded-2xl border border-dashed border-cream-line bg-white p-8 text-center text-[14px] text-neutral-600">
          No plans available right now. Check back soon.
        </p>
      ) : (
        <AutoRail className="mt-8 sm:mt-10" gridClassName="sm:grid-cols-2 sm:gap-5 md:grid-cols-3">
          {active.map((plan) => {
            const pv = priceView(plan);
            const perWash = plan.total_service_count > 0 ? pv.final / plan.total_service_count : null;
            const included = (plan.included_service_ids ?? [])
              .map((sid) => services.find((s) => s.id === sid)?.name)
              .filter((n): n is string => !!n);
            const vehicles = vehicleNames(plan.vehicle_types, vehicleTypes);
            const dark = plan.is_popular;
            const muted = dark ? "text-white/70" : "text-neutral-600";

            return (
              <div
                key={plan.id}
                className={`relative flex w-[80vw] max-w-[340px] shrink-0 snap-center sm:w-auto sm:max-w-none flex-col rounded-2xl border p-6 sm:p-7 ${
                  dark ? "border-black bg-black text-white" : "border-cream-line bg-white text-black"
                }`}
              >
                {dark && (
                  <span className="absolute right-5 top-5 rounded-full bg-gold px-2.5 py-1 text-[11px] font-bold text-black">
                    Most popular
                  </span>
                )}

                <p className={`text-[13px] font-medium capitalize ${muted}`}>{plan.billing_cycle}</p>
                <h3 className="mt-1 font-display text-[22px] font-bold leading-tight">{plan.name}</h3>

                <div className="mt-5 flex flex-wrap items-baseline gap-x-2">
                  {pv.varies && <span className={`text-[13px] ${muted}`}>from</span>}
                  <span className="font-display text-[34px] font-bold leading-none">{INR(pv.final)}</span>
                  {pv.original != null && (
                    <span className={`text-[14px] line-through ${dark ? "text-white/50" : "text-neutral-400"}`}>{INR(pv.original)}</span>
                  )}
                  <span className={`text-[13px] ${muted}`}>{CYCLE_LABEL[plan.billing_cycle]}</span>
                </div>

                <p className={`mt-2 text-[14px] ${muted}`}>
                  {plan.total_service_count} washes
                  {perWash != null && (
                    <>
                      {" · "}
                      {pv.varies ? "from " : ""}
                      {INR(perWash)} per wash
                    </>
                  )}
                </p>

                {plan.description && <p className={`mt-4 text-[14px] leading-relaxed ${muted}`}>{plan.description}</p>}

                <ul className="mt-5 space-y-2 text-[14px]">
                  {included.length > 0 && <PlanLine dark={dark}>{included.join(", ")}</PlanLine>}
                  {vehicles && <PlanLine dark={dark}>For {vehicles}</PlanLine>}
                  <PlanLine dark={dark}>Washed at your doorstep</PlanLine>
                </ul>

                <button
                  type="button"
                  onClick={choose}
                  className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl py-3 text-[14px] bg-gold font-bold text-white shadow-[0_6px_16px_rgba(232,169,0,0.24)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-gold-dark hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)]"
                >
                  Choose plan
                  <ArrowRight className="h-4 w-4" />
                </button>
              </div>
            );
          })}
        </AutoRail>
      )}
    </SectionShell>
  );
}

function PlanLine({ dark, children }: { dark: boolean; children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2">
      <Check className={`mt-[3px] h-4 w-4 shrink-0 ${dark ? "text-white" : "text-black"}`} strokeWidth={2.5} />
      <span className="min-w-0">{children}</span>
    </li>
  );
}
