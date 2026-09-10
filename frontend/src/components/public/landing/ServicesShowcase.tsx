import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Clock, Droplet, Wind, Sparkles, Armchair, Waves, Link2, Leaf } from "lucide-react";
import { catalogApi, vehicleTypeApi } from "../../../api/catalog";
import type { Service, VehicleTypeOption } from "../../../types";
import {
  INR,
  SectionHeader,
  SectionShell,
  groupServices,
  parseIncludes,
  priceView,
  serviceImage,
  vehicleLabel,
  type ServiceGroup,
} from "./shared";
import { AutoRail } from "./AutoRail";



/**
 * Landing-page services: image, what's included, final price, one button.
 * Black text only — the section is deliberately monochrome so the price
 * and the "Book" action are the only things that pull the eye. Variant
 * siblings (Bike Wash 1–5 bikes) collapse into one card; add-ons are not
 * shown here — the booking wizard offers them.
 */
export function ServicesShowcase({
  id = "services",
  limit,
  title = "Our services",
  subtitle = "Pick a service. We come to your doorstep and do the rest while you relax.",
}: {
  id?: string;
  limit?: number;
  title?: string;
  subtitle?: string;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

  const all = (data?.data ?? []).filter((s) => s.is_active !== false);
  const groups = groupServices(all);
  const shown = limit ? groups.slice(0, limit) : groups;
  const hasMore = limit ? groups.length > limit : false;

  return (
    <SectionShell id={id} className="bg-white">
      <SectionHeader
        title={title}
        subtitle={subtitle}
        action={
          hasMore ? (
            <Link to="/services" className="hidden items-center gap-1.5 text-[14px] font-semibold text-black underline-offset-4 hover:underline sm:inline-flex">
              All services
              <ArrowRight className="h-4 w-4" />
            </Link>
          ) : undefined
        }
      />

      {isLoading ? (
        <SkeletonGrid />
      ) : shown.length === 0 ? (
        <EmptyNotice />
      ) : (
        <AutoRail className="mt-8 sm:mt-10" gridClassName="sm:grid-cols-2 sm:gap-5 lg:grid-cols-3 lg:gap-6">
          {shown.map((g, i) => (
            <ServiceCard key={g.primary.id} group={g} index={i} vehicleTypes={vehicleTypes} />
          ))}
        </AutoRail>
      )}

      {hasMore && (
        <div className="mt-6 sm:hidden">
          <Link
            to="/services"
            className="flex w-full items-center justify-center gap-2 rounded-full border border-cream-line bg-white py-3 text-[14px] font-semibold text-black"
          >
            See all {groups.length} services
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      )}
    </SectionShell>
  );
}

function ServiceCard({
  group,
  index,
  vehicleTypes,
}: {
  group: ServiceGroup<Service>;
  index: number;
  vehicleTypes: VehicleTypeOption[] | undefined;
}) {
  const s = group.primary;
  const pv = priceView(s);
  const { items } = parseIncludes(s.description);
  const vehicle = vehicleLabel(s.vehicle_types, vehicleTypes);
  const hasVariants = group.variants.length > 1;
  const showFrom = pv.varies || hasVariants;

  return (
    <Link
      to={`/book?serviceId=${s.id}`}
      aria-label={`Book ${s.name}`}
      className="group flex h-full w-[80vw] max-w-[340px] shrink-0 snap-center flex-col overflow-hidden rounded-2xl border border-cream-line bg-white text-left transition-all duration-300 hover:-translate-y-0.5 hover:border-[#DACFB9] hover:shadow-[0_18px_40px_-18px_rgba(60,40,0,0.25)] sm:w-auto sm:max-w-none"
    >
      <div className="relative aspect-[16/11] w-full bg-neutral-100">
        <img
          src={serviceImage(s, index)}
          alt={s.name}
          loading="lazy"
          className="absolute inset-0 h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.03]"
        />
        <span className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-full bg-gold px-2.5 py-1 text-[11px] font-bold text-black shadow-sm">
          <Clock className="h-3 w-3" />
          {s.duration_minutes} min
        </span>
      </div>

      <div className="flex min-w-0 flex-1 flex-col p-4 sm:p-5">
        <h3 className="font-display text-[16px] font-bold leading-snug text-black sm:text-[18px]">{s.name}</h3>

        {vehicle && <p className="mt-1 text-[12px] text-neutral-500 sm:text-[13px]">For {vehicle.toLowerCase()}</p>}

        {(() => {
          let features = items.slice(0, 3).map(text => ({ text, Icon: Check }));
          const n = s.name.toLowerCase();
          
          if (n.includes("waterless")) {
            features = [
              { text: "Waterless exterior clean", Icon: Leaf },
              { text: "Interior vacuum", Icon: Wind },
              { text: "Dashboard polish", Icon: Sparkles }
            ];
          } else if (n.includes("star wash")) {
            features = [
              { text: "Exterior foam wash", Icon: Droplet },
              { text: "Interior vacuum", Icon: Wind },
              { text: "Dashboard polish", Icon: Sparkles }
            ];
          } else if (n.includes("deep cleaning")) {
            features = [
              { text: "Foam wash, vacuum & dashboard polish", Icon: Droplet },
              { text: "Seat cleaning", Icon: Armchair },
              { text: "Floor & mats cleaning", Icon: Waves }
            ];
          } else if (n.includes("jet wash")) {
            const hasUnderbody = items.some(i => /underbody|undebody/i.test(i));
            const third = hasUnderbody ? "Undebody rinse" : (items.find(i => !/exterior|foam|tyre|tire|polish/i.test(i)) || "Undebody rinse");
            features = [
              { text: "Exterior foam wash", Icon: Droplet },
              { text: "Tyre polish", Icon: Sparkles },
              { text: third, Icon: Waves }
            ];
          } else if (n.includes("bike wash")) {
            features = [
              { text: "Bike foam wash", Icon: Droplet },
              { text: "Tyre polish", Icon: Sparkles },
              { text: "Chain cleaning", Icon: Link2 }
            ];
          }

          // Ensure exactly 3 items to keep layout consistent
          while (features.length < 3) {
            features.push({ text: "-", Icon: Check });
          }
          
          return (
            <ul className="mt-4 space-y-2.5">
              {features.slice(0, 3).map((feat, idx) => (
                <li key={idx} className={`flex items-start gap-3 text-[13px] leading-snug ${feat.text === "-" ? "invisible" : "text-neutral-800"}`}>
                  <span className="flex mt-0.5 h-5 w-5 shrink-0 items-center justify-center rounded-full bg-neutral-100 text-black">
                    <feat.Icon className="h-3 w-3" strokeWidth={2.5} />
                  </span>
                  <span className="min-w-0 flex-1 pt-0.5">{feat.text}</span>
                </li>
              ))}
            </ul>
          );
        })()}

        {hasVariants && (
          <p className="mt-3 text-[12px] leading-snug text-neutral-600">
            {group.variants.map((v) => `${v.variant_label ?? v.name} ${INR(priceView(v).final)}`).join(" · ")}
          </p>
        )}

        <div className="mt-6 flex-1" />
        <div className="flex items-end justify-between gap-3 border-t border-cream-line-soft pt-4 mt-auto">
          <div>
            {pv.offerLabel && <span className="block text-[11px] font-medium text-neutral-500">{pv.offerLabel}</span>}
            <div className="flex flex-wrap items-baseline gap-x-2">
              {showFrom && <span className="text-[12px] text-neutral-500">from</span>}
              <span className="font-display text-[20px] font-bold leading-none text-black sm:text-[22px]">{INR(pv.final)}</span>
              {pv.original != null && <span className="text-[13px] text-neutral-400 line-through">{INR(pv.original)}</span>}
            </div>
          </div>

          <span className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-gold px-7 py-2.5 text-[13px] font-bold text-white shadow-[0_6px_16px_rgba(232,169,0,0.24)] transition-all duration-200 group-hover:-translate-y-0.5 group-hover:bg-gold-dark group-hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)]">
            Book now
            <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
          </span>
        </div>
      </div>
    </Link>
  );
}

function SkeletonGrid() {
  return (
    <AutoRail className="mt-8 sm:mt-10" gridClassName="sm:grid-cols-2 sm:gap-5 lg:grid-cols-3 lg:gap-6" intervalMs={600000}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex h-full w-[80vw] max-w-[340px] shrink-0 snap-center animate-pulse flex-col overflow-hidden rounded-2xl border border-cream-line bg-white sm:w-auto sm:max-w-none" aria-hidden="true">
          <div className="aspect-[16/11] w-full bg-neutral-100" />
          <div className="flex-1 space-y-3 p-4 sm:p-5">
            <div className="h-4 w-2/3 rounded bg-neutral-100" />
            <div className="h-3 w-4/5 rounded bg-neutral-100" />
            <div className="h-3 w-3/4 rounded bg-neutral-100" />
            <div className="h-3 w-3/5 rounded bg-neutral-100" />
            <div className="flex items-end justify-between pt-3">
              <div className="h-6 w-16 rounded bg-neutral-100" />
              <div className="h-8 w-20 rounded-full bg-neutral-100" />
            </div>
          </div>
        </div>
      ))}
    </AutoRail>
  );
}

function EmptyNotice() {
  return (
    <div className="mt-8 rounded-2xl border border-dashed border-cream-line bg-white p-8 text-center">
      <p className="text-[15px] font-semibold text-black">Our service list is being updated.</p>
      <p className="mt-1 text-[14px] text-neutral-600">You can still book — tell us what your vehicle needs.</p>
      <Link to="/book" className="mt-5 inline-flex items-center gap-2 rounded-xl px-5 py-3 text-[14px] bg-gold font-bold text-white shadow-[0_6px_16px_rgba(232,169,0,0.24)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-gold-dark">
        Book now
        <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}
