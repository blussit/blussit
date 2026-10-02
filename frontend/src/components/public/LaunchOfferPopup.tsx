import { useEffect, useState } from "react";
import { ArrowRight, Check, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { bikeTypeIds } from "../../lib/serviceMix";
import type { Service, VehicleTypeOption } from "../../types";
import { DiscountBadge, OfferTag, discountPercent } from "../ui";
import {
  INR,
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
export function usePromotedOffer(): PromotedOffer | null {
  const { data } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });

  const {
    data: vehicleTypes,
    isPending: typesPending,
  } = useQuery({
    queryKey: ["vehicle-types"],
    queryFn: () => vehicleTypeApi.list(),
  });

  const service = (data?.data ?? []).find(
    (s) =>
      s.is_active !== false &&
      !s.is_addon &&
      s.offer_tag?.trim()
  );

  const tag = service?.offer_tag?.trim();

  if (!service || !tag || typesPending) return null;

  const cars = carPrices(service, vehicleTypes);

  if (!cars.length) {
    const pv = priceView(service);

    return {
      service,
      tag,
      price: pv.final,
      original: pv.original,
      samePriceForAllCars: !pv.varies,
    };
  }

  const best = cars.reduce((a, b) =>
    b.price < a.price ? b : a
  );

  return {
    service,
    tag,
    price: best.price,
    original: best.original,
    samePriceForAllCars:
      new Set(cars.map((c) => c.price)).size === 1,
  };
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
            label={offer.tag}
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

  const close = () => setOpen(false);

  const claim = () => {
    close();
    onClaim();
  };

  const name = titleCase(offer.service.name);

  const facts = [
    offer.samePriceForAllCars && "Same price for every car",
    offer.service.prepaid_only && "Pay online to book",
  ].filter((f): f is string => !!f);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[100000] flex items-center justify-center p-4 sm:p-6">
          {/* Backdrop */}
          <motion.div
            className="absolute inset-0 bg-[#071A3D]/60 backdrop-blur-[5px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={close}
          />

          {/* Popup */}
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="offer-popup-title"
            initial={{ opacity: 0, y: 22, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="relative z-10 w-full max-w-[720px] overflow-hidden rounded-[24px] border border-[#E2ECFA] bg-white text-[#071A3D] shadow-[0_30px_90px_rgba(7,26,61,0.28)]"
          >
            {/* Subtle blue top accent */}

            {/* Close */}
            <button
              type="button"
              onClick={close}
              aria-label="Close offer"
              className="absolute right-4 top-4 z-30 flex h-10 w-10 items-center justify-center rounded-full border border-[#D7E5F7] bg-white text-[#1677FF] shadow-[0_4px_16px_rgba(7,26,61,0.10)] transition-all hover:border-[#1677FF] hover:bg-[#F2F7FF]"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr]">
              {/* Offer image */}
              <div className="relative h-[230px] overflow-hidden bg-[#EEF6FF] sm:h-[390px]">
                <img
                  src={serviceImage(offer.service, 0)}
                  alt={name}
                  decoding="async"
                  className="absolute inset-0 h-full w-full object-cover object-center"
                />

                {/* Very light blue overlay for brand consistency */}
                <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(7,26,61,0.02)_0%,rgba(22,119,255,0.08)_100%)]" />

                {/* Small visual label */}
                <div className="absolute bottom-4 left-4 rounded-full border border-white/70 bg-white/90 px-3 py-1.5 text-xs font-bold text-[#1677FF] shadow-sm backdrop-blur-sm">
                  BLUSSIT · Doorstep Car Care
                </div>
              </div>

              {/* Content */}
              <div className="flex flex-col justify-center p-6 sm:p-8">
                <div className="pr-8">
                  <OfferTag
                    label={offer.tag}
                    className="border border-[#BFD8FF] bg-[#EEF6FF] text-[#1677FF] ring-0"
                  />

                  <h2
                    id="offer-popup-title"
                    className="mt-4 text-[30px] font-black leading-[1.05] tracking-[-0.02em] text-[#071A3D] sm:text-[36px]"
                  >
                    {name}
                  </h2>

                  <div className="mt-4 flex flex-wrap items-center gap-x-2.5 gap-y-2">
                    {!offer.samePriceForAllCars && (
                      <span className="text-sm font-medium text-[#64748B]">
                        From
                      </span>
                    )}

                    <span className="text-[30px] font-black leading-none text-[#1677FF]">
                      {INR(offer.price)}
                    </span>

                    {offer.original != null && (
                      <span className="text-base font-medium text-[#94A3B8] line-through">
                        {INR(offer.original)}
                      </span>
                    )}

                    <DiscountBadge
                      percent={discountPercent(
                        offer.price,
                        offer.original
                      )}
                    />
                  </div>

                  {facts.length > 0 && (
                    <ul className="mt-5 space-y-3 text-sm font-medium text-[#475569]">
                      {facts.map((f) => (
                        <li
                          key={f}
                          className="flex items-center gap-2.5"
                        >
                          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[#EAF3FF]">
                            <Check
                              className="h-3.5 w-3.5 text-[#1677FF]"
                              strokeWidth={2.8}
                            />
                          </span>
                          {f}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <button
                  type="button"
                  onClick={claim}
                  className="mt-7 flex w-full items-center justify-center gap-2 rounded-[12px] bg-[#1677FF] px-4 py-3 text-sm font-bold text-white shadow-[0_8px_22px_rgba(22,119,255,0.22)] transition-all hover:-translate-y-0.5 hover:bg-[#086BEF] active:translate-y-0"
                >
                  Book now
                  <ArrowRight className="h-4 w-4" />
                </button>

                <button
                  type="button"
                  onClick={close}
                  className="mt-3 w-full text-center text-xs font-medium text-[#64748B] transition-colors hover:text-[#1677FF]"
                >
                  Maybe later
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
