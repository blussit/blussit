import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check } from "lucide-react";
import { subscriptionApi } from "../../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../../api/catalog";
import { useAuth } from "../../../context/AuthContext";
import { CustomPlanEnquiryModal } from "../../shared/CustomPlanEnquiryModal";
import { passHeadlinePrice } from "../../../lib/passPricing";
import { bikeTypeIds } from "../../../lib/serviceMix";
import { INR, SectionHeader, SectionShell } from "./shared";
import type { Service } from "../../../types";

/**
 * Two cards, always (founder call): the monthly pass, and "something else".
 * A pass is bought for ONE car and covers ONE wash, so the price on a card
 * can only ever be a "from" — the real figure is quoted at purchase, once
 * the buyer has named the car and the wash.
 *
 * Anyone the standard pass doesn't fit — a fleet, a different rhythm — goes
 * to the second card rather than off the site.
 */
export function PlansShowcase({ id = "plans", showEmpty = false }: { id?: string; showEmpty?: boolean }) {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [enquiryOpen, setEnquiryOpen] = useState(false);

  const { data: plans, isLoading } = useQuery({ queryKey: ["public-plans"], queryFn: () => subscriptionApi.plans(true) });
  const { data: servicesData } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

  const services = servicesData?.data ?? [];
  // Monthly only — quarterly/yearly plans are retired and kept inactive.
  const active = (plans ?? []).filter((p) => p.is_active !== false && p.billing_cycle === "monthly");

  if (!isLoading && active.length === 0 && !showEmpty) return null;

  const choose = () => navigate(user?.role === "customer" ? "/app/subscriptions" : "/login");

  // "Two cards, always" (founder call): ONE pass card, whatever the exact
  // washes/vehicle-types behind it — never one card per plan document. An
  // admin may model the pass as several plan docs (one per vehicle type or
  // wash), so this collapses all of them into a single "Monthly Pass" tile
  // quoting the cheapest combination across every one of them, instead of
  // showing a separate, narrower, oddly-named card per document.
  //
  // This card is explicitly framed as "one CAR per pass" (see the copy
  // below) — a bike wash riding on the same plan document is a much
  // cheaper, unrelated product and would otherwise drag the headline
  // price down to a number no car owner (the audience this card is
  // written for) can actually get. Bike-only vehicle types are excluded
  // here so the price always reflects an actual car wash (e.g. a
  // Hatchback Jet Wash), matching what the card lists and promises; bike
  // owners still see accurate bike pricing on the real purchase page.
  const bikeIds = bikeTypeIds(vehicleTypes);

  // The headline number (see passHeadlinePrice) is deliberately always
  // the Waterless Service × Hatchback price — pulled live from whatever
  // the admin has set under Subscription Plans, the same figure shown on
  // the customer's own "Get a pass" purchase page, never a different
  // wash's price even if one happens to be priced cheaper.
  const fromPrices = active.map((plan) => passHeadlinePrice(plan, services, vehicleTypes)).filter((n): n is number => n != null);
  const from = fromPrices.length ? Math.min(...fromPrices) : null;
  const washes = Array.from(
    new Set(
      active
        .flatMap((plan) => plan.included_service_ids ?? [])
        .map((sid) => services.find((s) => s.id === sid))
        .filter((s): s is Service => !!s && !(s.vehicle_types?.length && s.vehicle_types.every((t) => bikeIds.has(t))))
        .map((s) => s.name)
    )
  );
  const maxWashesAMonth = Math.max(0, ...active.map((p) => p.total_service_count || 0));

  return (
    <SectionShell id={id} className="bg-white">
      <SectionHeader
        title="Monthly Pass"
        subtitle="One car, one wash, one monthly price. Book whenever you need it."
      />

      <div className="mt-8 grid grid-cols-1 gap-5 sm:mt-10 sm:grid-cols-2">
        {isLoading ? (
          <div className="h-[320px] animate-pulse rounded-2xl border border-cream-line bg-white" aria-hidden="true" />
        ) : (
          <div className="flex flex-col rounded-2xl border border-black bg-black p-6 text-white sm:p-7">
            <h3 className="font-display text-[22px] font-bold leading-tight">Monthly Pass</h3>

            <div className="mt-5 flex flex-wrap items-baseline gap-x-2">
              {from != null && <span className="text-[13px] text-white/70">From</span>}
              <span className="font-display text-[34px] font-bold leading-none">{from != null ? INR(from) : "—"}</span>
              <span className="text-[13px] text-white/70">Per Month</span>
            </div>
            <p className="mt-2 text-[14px] text-white/70">
              {maxWashesAMonth > 0 ? `Up to ${maxWashesAMonth} washes a month` : "Washes every month"} · your price depends on your car and the wash you pick
            </p>

            <ul className="mt-5 space-y-2 text-[14px]">
              {washes.length > 0 && <PlanLine dark>Choose one: {washes.join(", ")}</PlanLine>}
              <PlanLine dark>One car per pass — take one for each of your cars</PlanLine>
              <PlanLine dark>Washed at your doorstep, whenever you book</PlanLine>
            </ul>

            <button
              type="button"
              onClick={choose}
              className="mt-6 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gold py-3 text-[14px] font-bold text-white shadow-[0_6px_16px_rgba(232,169,0,0.24)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-gold-dark hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)]"
            >
              Get This Pass
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Always present, even with no plans configured — it's the route
            for everyone the standard pass can't serve. */}
        <div className="flex flex-col rounded-2xl border border-cream-line bg-white p-6 text-black sm:p-7">
          <h3 className="font-display text-[22px] font-bold leading-tight">Custom Plan</h3>
          <p className="mt-5 text-[14px] leading-relaxed text-neutral-600">
            More cars, more washes, or a fixed time every week? Tell us what you need and we'll price it for you.
          </p>
          <ul className="mt-5 space-y-2 text-[14px]">
            <PlanLine dark={false}>Any number of vehicles</PlanLine>
            <PlanLine dark={false}>Your own schedule</PlanLine>
            <PlanLine dark={false}>We call you back with a price</PlanLine>
          </ul>
          <button
            type="button"
            onClick={() => setEnquiryOpen(true)}
            className="mt-auto inline-flex w-full items-center justify-center gap-2 rounded-xl border border-black py-3 text-[14px] font-bold text-black transition-colors hover:bg-black hover:text-white"
          >
            Request a Custom Plan
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      <CustomPlanEnquiryModal
        open={enquiryOpen}
        onClose={() => setEnquiryOpen(false)}
        defaultName={user?.full_name}
        defaultPhone={user?.phone}
      />
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
