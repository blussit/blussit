import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Droplet, Car, CalendarCheck, Users, CalendarClock, FileText } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { subscriptionApi } from "../../../api/engagement";
import { catalogApi, vehicleTypeApi } from "../../../api/catalog";
import { useAuth } from "../../../context/AuthContext";
import { CustomPlanEnquiryModal } from "../../shared/CustomPlanEnquiryModal";
import { bikeTypeIds } from "../../../lib/serviceMix";
import { titleCase } from "./shared";
import { SocietyRequestCard } from "./SocietyRequestCard";
import type { Service } from "../../../types";

const MONTHLY_IMG = "/img/plans-card1-800.webp";
const CUSTOM_IMG = "/img/plans-card2-800.webp";

/**
 * Three cards, always: the monthly pass, housing societies, and a custom plan.
 * A pass is bought for ONE car and covers ONE wash, so the price is only a "from".
 */
export function PlansShowcase({ id = "plans" }: { id?: string; showEmpty?: boolean }) {
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
  const active = (plans ?? []).filter((p) => p.is_active !== false);

  // No monthly pass on sale: the cards will still render (showing fallback pricing if needed),
  // so the user can always see the Monthly Pass and Custom Plan options alongside the Society block.

  const choose = () =>
    user?.role === "customer" ? navigate("/app/subscriptions") : navigate("/login", { state: { from: { pathname: "/app/subscriptions" } } });

  // Bike-only vehicle types are excluded so the headline always reflects a car wash.
  const bikeIds = bikeTypeIds(vehicleTypes);

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
  const washesLine = maxWashesAMonth > 0 ? `Up to ${maxWashesAMonth} washes a month` : "Washes every month";

  return (
    <section id={id || "plans"} className="bg-white pt-4 pb-4 md:pt-6 md:pb-6 lg:pt-8 lg:pb-8">
      <div className="mx-auto w-full max-w-[1400px] px-5 sm:px-6">
        {/* Header */}
        <div className="flex flex-col text-left">
          <div>
            <span className="inline-block rounded-full bg-[#EEF4FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[1.2px] text-[#1677FF]">
              Monthly Pass
            </span>
          </div>
          <h2 className="mt-2.5 font-display text-[24px] font-extrabold leading-[1.15] text-[#071A3D] sm:text-[28px] lg:text-[32px]">
            Keep Your Car Clean,
            <br className="sm:hidden" />{" "}
            <span className="relative inline-block text-[#1677FF]">
              Every Month.
              <svg viewBox="0 0 200 12" preserveAspectRatio="none" className="absolute -bottom-1.5 left-2 h-[8px] w-[75%]" aria-hidden="true">
                <path d="M2,8 C50,2 120,2 198,7" fill="none" stroke="#FACC15" strokeWidth="3" strokeLinecap="round" />
              </svg>
            </span>
          </h2>
          <p className="mt-2 max-w-[680px] text-[14px] text-[#64748B] sm:text-[15px]">
            Save more with our convenient monthly car care plans. Hassle-free, doorstep service.
          </p>
        </div>

        {/* Cards */}
        {/* Three equal columns, equal heights — every button lines up on one row. */}
        <div className="mt-6 grid grid-cols-1 items-stretch gap-5 lg:mt-8 lg:grid-cols-3 lg:gap-6">
          {isLoading ? (
            <div className="h-[380px] animate-pulse rounded-[22px] lg:h-full border border-[#E4E9F0] bg-white" aria-hidden="true" />
          ) : (
            <div className="group relative flex flex-col overflow-hidden rounded-[22px] border border-[#E4E9F0] bg-white text-[#071A3D] shadow-[0_6px_22px_rgba(15,30,60,0.05)] sm:flex-row lg:h-full lg:flex-col">
              <div className="relative z-10 flex flex-1 flex-col p-5 sm:w-[56%] sm:flex-none sm:p-6 lg:w-full lg:flex-1 lg:p-7">
                <h3 className="font-display text-[22px] font-extrabold leading-tight sm:text-[26px]">Monthly Pass</h3>
                <p className="mt-1 text-[13px] text-[#64748B]">Price depends on your car and wash</p>

                <div className="mt-1.5 flex items-baseline gap-1.5">
                  <span className="font-display text-[36px] font-extrabold leading-none text-[#1677FF] sm:text-[40px]">
                    ₹999
                  </span>
                  <span className="text-[13px] text-[#64748B]">/ Month</span>
                </div>

                <ul className="my-3 space-y-1.5">
                  <PlanLine
                    Icon={Droplet}
                    title="Choose Your Service"
                    sub={washes.length > 0 ? washes.map((w) => titleCase(w)).join(", ") : "Pick the wash that fits your car"}
                  />
                  <PlanLine Icon={Car} title="One Car Per Pass" sub={washesLine} />
                  <PlanLine Icon={CalendarCheck} title="At Your Doorstep" sub="Book anytime, we come to you" />
                </ul>

                <button
                  type="button"
                  onClick={choose}
                  className="mt-auto inline-flex w-full items-center justify-center gap-2 rounded-full border border-[#1677FF]/50 bg-white py-3 text-[14.5px] font-bold text-[#1677FF] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#F4F8FF]"
                >
                  Subscribe Now
                  <ArrowRight className="h-4 w-4" strokeWidth={2.5} />
                </button>
              </div>

              <div
                className="relative order-first h-[160px] w-full shrink-0 overflow-hidden [mask-image:linear-gradient(to_bottom,black_55%,transparent)] sm:absolute sm:inset-y-0 sm:right-0 sm:order-none sm:h-auto sm:w-[52%] sm:[mask-image:linear-gradient(to_right,transparent,black_48%)] lg:relative lg:h-[180px] lg:w-full lg:order-first lg:[mask-image:linear-gradient(to_bottom,black_55%,transparent)]"
              >
                <div
                  className="absolute inset-0 bg-cover bg-center transition-transform duration-700 group-hover:scale-105"
                  style={{ backgroundImage: `url(${MONTHLY_IMG})` }}
                />
              </div>
            </div>
          )}

          <SocietyRequestCard />

          {/* Custom plan */}
          <div className="group relative flex flex-col overflow-hidden rounded-[22px] border border-[#E4E9F0] bg-white text-[#071A3D] shadow-[0_6px_22px_rgba(15,30,60,0.05)] sm:flex-row lg:h-full lg:flex-col">
            <div className="relative z-10 flex flex-1 flex-col p-5 sm:w-[56%] sm:flex-none sm:p-6 lg:w-full lg:flex-1 lg:p-7">
              <h3 className="font-display text-[22px] font-extrabold leading-tight sm:text-[24px]">Custom Plan</h3>
              <p className="mt-1 text-[13px] leading-snug text-[#64748B]">
                More cars, more washes, or a fixed time every week? Tell us what you need and we'll create a plan for you.
              </p>

              <ul className="mb-5 mt-5 space-y-3">
                <PlanLine Icon={Users} title="Any Number Of Vehicles" sub="Personal or business fleets" />
                <PlanLine Icon={CalendarClock} title="Your Own Schedule" sub="Weekly, bi-weekly or custom" />
                <PlanLine Icon={FileText} title="We Call You Back" sub="With the best price for your needs" />
              </ul>

              <button
                type="button"
                onClick={() => setEnquiryOpen(true)}
                className="mt-auto inline-flex w-full items-center justify-center gap-2 rounded-full border border-[#1677FF]/50 bg-white py-3 text-[14.5px] font-bold text-[#1677FF] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#F4F8FF]"
              >
                Request Custom Plan
                <ArrowRight className="h-4 w-4" strokeWidth={2.5} />
              </button>
            </div>

            <div
                className="relative order-first h-[160px] w-full shrink-0 overflow-hidden [mask-image:linear-gradient(to_bottom,black_55%,transparent)] sm:absolute sm:inset-y-0 sm:right-0 sm:order-none sm:h-auto sm:w-[52%] sm:[mask-image:linear-gradient(to_right,transparent,black_48%)] lg:relative lg:h-[180px] lg:w-full lg:order-first lg:[mask-image:linear-gradient(to_bottom,black_55%,transparent)]"
              >
                <div
                  className="absolute inset-0 bg-cover bg-center transition-transform duration-700 group-hover:scale-105"
                  style={{ backgroundImage: `url(${CUSTOM_IMG})` }}
                />
              </div>
          </div>
        </div>
      </div>

      <CustomPlanEnquiryModal
        open={enquiryOpen}
        onClose={() => setEnquiryOpen(false)}
        defaultName={user?.full_name}
        defaultPhone={user?.phone}
      />
    </section>
  );
}

function PlanLine({ Icon, title, sub }: { Icon: LucideIcon; title: string; sub: string }) {
  return (
    <li className="flex items-center gap-3">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#EEF4FF]">
        <Icon className="h-[18px] w-[18px] text-[#1677FF]" strokeWidth={1.8} />
      </span>
      <span className="min-w-0 leading-tight">
        <span className="block text-[13.5px] font-bold text-[#071A3D]">{title}</span>
        <span className="mt-0.5 block text-[12px] text-[#64748B]">{sub}</span>
      </span>
    </li>
  );
}