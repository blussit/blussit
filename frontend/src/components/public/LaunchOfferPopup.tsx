import { useEffect, useState } from "react";
import { ArrowRight, Check, X, Zap } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";
import { bikeTypeIds } from "../../lib/serviceMix";
import type { Service, VehicleTypeOption } from "../../types";
import { DiscountBadge, OfferTag, discountPercent } from "../ui";
import {
  INR,
  parseIncludes,
  priceForType,
  priceView,
  serviceImage,
  titleCase,
} from "./landing/shared";

const SEEN_KEY = "blussit:offerPopupSeen";

export interface PromotedOffer {
  service: Service;
  tag: string;
  /** Lowest car price, and its struck MRP. */
  price: number;
  original: number | null;
  /** Every car type pays the same price. */
  samePriceForAllCars: boolean;
}

function carPrices(
  s: Service,
  types: VehicleTypeOption[] | undefined
) {
  const bikes = bikeTypeIds(types);
  const offered = s.vehicle_types?.length
    ? s.vehicle_types
    : (types ?? []).map((t) => t.id);

  return offered
    .filter((id) => !bikes.has(id))
    .map((id) => priceForType(s, id));
}

/**
 * The service the landing offer promotes: the first active main service
 * the admin has given an offer tag, headlined at its cheapest car price.
 */
function toOffer(service: Service, vehicleTypes: VehicleTypeOption[] | undefined): PromotedOffer {
  const tag = (service.offer_tag || "").trim();
  const cars = carPrices(service, vehicleTypes);

  if (!cars.length) {
    const pv = priceView(service);
    return { service, tag, price: pv.final, original: pv.original, samePriceForAllCars: !pv.varies };
  }

  const best = cars.reduce((a, b) => (b.price < a.price ? b : a));
  return {
    service,
    tag,
    price: best.price,
    original: best.original,
    samePriceForAllCars: new Set(cars.map((c) => c.price)).size === 1,
  };
}

/** Every active main service the admin has given an offer tag, in catalogue
 * order — the landing offer bar lists them all. */
export function useActiveOffers(): PromotedOffer[] {
  const { data } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: vehicleTypes, isPending: typesPending } = useQuery({
    queryKey: ["vehicle-types"],
    queryFn: () => vehicleTypeApi.list(),
  });
  if (typesPending) return [];
  return (data?.data ?? [])
    .filter((s) => s.is_active !== false && !s.is_addon && s.offer_tag?.trim())
    .map((s) => toOffer(s, vehicleTypes));
}

/** The first of those — what the one-time popup promotes. */
export function usePromotedOffer(): PromotedOffer | null {
  return useActiveOffers()[0] ?? null;
}

export function LaunchOfferStrip({
  offer,
  onClaim,
}: {
  offer: PromotedOffer;
  onClaim: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClaim}
      className="group relative z-20 w-full overflow-hidden bg-[#1677FF] text-white shadow-[0_12px_28px_rgba(22,119,255,0.20)]"
    >
      <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(110deg,transparent_0%,rgba(255,255,255,0.14)_24%,transparent_45%)] animate-sheen-drift" />
      <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/35" />

      <div className="container-page relative flex min-h-[48px] items-center justify-center gap-2 overflow-hidden py-2 text-center sm:gap-3">
        <span className="hidden sm:inline-flex">
          <OfferTag
            label={titleCase(offer.tag)}
            className="ring-1 ring-white/40"
          />
        </span>

        <span className="min-w-0 truncate text-sm font-bold sm:text-base">
          {titleCase(offer.service.name)}
        </span>

        <span className="shrink-0 text-sm font-black sm:text-base">
          {!offer.samePriceForAllCars && (
            <span className="mr-1 font-medium text-white/70">
              from
            </span>
          )}
          {INR(offer.price)}
        </span>

        {offer.original != null && (
          <span className="hidden shrink-0 text-sm text-white/55 line-through sm:inline">
            {INR(offer.original)}
          </span>
        )}

        <DiscountBadge
          percent={discountPercent(
            offer.price,
            offer.original
          )}
        />

        <ArrowRight className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-1" />
      </div>
    </button>
  );
}

export function LaunchOfferPopup({
  offer,
  onClaim,
}: {
  offer: PromotedOffer;
  onClaim: () => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(SEEN_KEY)) return;
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      // Storage blocked: still show it, once per page view.
    }

    setOpen(true);
  }, []);

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };

    window.addEventListener("keydown", onKey);

    return () =>
      window.removeEventListener("keydown", onKey);
  }, [open]);

  // The page behind stays still while the offer is up.
  useBodyScrollLock(open);

  const close = () => setOpen(false);

  const claim = () => {
    close();
    onClaim();
  };

  const name = titleCase(offer.service.name);
  const off = offer.original && offer.original > offer.price ? Math.round(((offer.original - offer.price) / offer.original) * 100) : null;
  // Ticks = what's included (the admin's description list); the booking
  // terms sit on one plain line under them.
  const { summary, items: included } = parseIncludes(offer.service.description);
  const terms = [
    offer.service.prepaid_only && "Pay Online",
    offer.service.duration_minutes && `${offer.service.duration_minutes} Mins Service`,
  ].filter((f): f is string => !!f);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[100000] flex items-center justify-center overflow-y-auto overscroll-contain p-4 sm:p-6">
          <motion.div
            className="fixed inset-0 bg-[#0E1A33]/55 backdrop-blur-[4px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={close}
          />

          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="offer-popup-title"
            initial={{ opacity: 0, y: 22, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="relative z-10 m-auto w-full max-w-[560px] overflow-hidden rounded-[22px] bg-white shadow-[0_30px_90px_rgba(14,26,51,0.30)]"
          >
            <button
              type="button"
              onClick={close}
              aria-label="Close offer"
              className="absolute right-4 top-4 z-30 flex h-10 w-10 items-center justify-center rounded-full border border-[#D6E4FA] bg-white text-[#0A66F0] shadow-[0_4px_14px_rgba(14,26,51,0.10)] transition-colors hover:bg-[#F2F7FF]"
            >
              <X className="h-[18px] w-[18px]" strokeWidth={2.4} />
            </button>

            <div className="grid grid-cols-1 sm:grid-cols-[43%_57%]">
              <div className="relative h-[170px] overflow-hidden bg-[#EEF3FA] sm:h-auto sm:min-h-[410px]">
                <img
                  src={serviceImage(offer.service, 0)}
                  alt={name}
                  decoding="async"
                  className="absolute inset-0 h-full w-full object-cover"
                />
              </div>

              <div className="flex flex-col justify-center px-6 pb-6 pt-5 sm:px-7 sm:py-8">
                <span className="inline-flex w-fit items-center gap-1.5 rounded-full px-3 py-1.5 text-[11.5px] font-extrabold uppercase tracking-wide text-[#0E1A33]" style={{ backgroundColor: "#FFD21F" }}>
                  <Zap className="h-3.5 w-3.5" fill="currentColor" strokeWidth={0} />
                  {offer.tag}
                </span>

                <h2 id="offer-popup-title" className="mt-3 pr-10 font-display text-[30px] font-extrabold leading-[1.1] tracking-[-0.02em] text-[#0E1A33] sm:text-[32px]">
                  {name}
                </h2>
                <p className="mt-1.5 text-[15px] leading-[1.45] text-[#5F6878]">{summary || "Done right at your doorstep."}</p>

                <div className="mt-4 flex flex-wrap items-center gap-x-2.5 gap-y-2">
                  {!offer.samePriceForAllCars && <span className="text-sm font-medium text-[#5F6878]">From</span>}
                  <span className="text-[34px] font-extrabold leading-none text-[#0A66F0]">{INR(offer.price)}</span>
                  {offer.original != null && offer.original > offer.price && (
                    <span className="text-[17px] font-medium text-[#94A3B8] line-through">{INR(offer.original)}</span>
                  )}
                  {off != null && (
                    <span className="rounded-full px-3 py-1 text-[13px] font-extrabold text-[#0E1A33]" style={{ backgroundColor: "#FFD21F" }}>
                      {off}% OFF
                    </span>
                  )}
                </div>

                {included.length > 0 && (
                  <ul className="mt-5 space-y-2.5 text-[14.5px] text-[#3A4456]">
                    {included.slice(0, 4).map((f) => (
                      <li key={f} className="flex items-center gap-2.5">
                        <Check className="h-4 w-4 shrink-0 text-[#0A66F0]" strokeWidth={3.2} />
                        {titleCase(f)}
                      </li>
                    ))}
                  </ul>
                )}
                {terms.length > 0 && <p className="mt-3 text-[13.5px] text-[#5F6878]">{terms.join(" · ")}</p>}

                <button
                  type="button"
                  onClick={claim}
                  className="mt-6 flex h-[50px] w-full items-center justify-center gap-2 rounded-[14px] bg-[#0A66F0] px-4 text-[15px] font-bold text-white shadow-[0_10px_24px_rgba(10,102,240,0.28)] transition-all hover:-translate-y-0.5 hover:bg-[#0858D0] active:translate-y-0"
                >
                  Book {name}
                  <ArrowRight className="h-[18px] w-[18px]" strokeWidth={2.4} />
                </button>
                <button type="button" onClick={close} className="mt-3 w-full text-center text-[13px] font-medium text-[#5F6878] hover:text-[#0A66F0]">
                  Maybe Later
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
