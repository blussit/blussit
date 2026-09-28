import { useEffect, useState } from "react";
import { ArrowRight, Check, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { catalogApi, vehicleTypeApi } from "../../api/catalog";
import { bikeTypeIds } from "../../lib/serviceMix";
import type { Service, VehicleTypeOption } from "../../types";
import { DiscountBadge, OfferTag, discountPercent } from "../ui";
import { INR, priceForType, priceView, serviceImage, titleCase } from "./landing/shared";

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

function carPrices(s: Service, types: VehicleTypeOption[] | undefined) {
  const bikes = bikeTypeIds(types);
  const offered = s.vehicle_types?.length ? s.vehicle_types : (types ?? []).map((t) => t.id);
  return offered.filter((id) => !bikes.has(id)).map((id) => priceForType(s, id));
}

/**
 * The service the landing offer promotes: the first active main service the
 * admin has given an offer tag, headlined at its cheapest car price. Null
 * when nothing is tagged — then no popup and no strip.
 */
export function usePromotedOffer(): PromotedOffer | null {
  // Same query as the service cards so the landing page fetches once (the API defaults to active_only).
  const { data } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const { data: vehicleTypes, isPending: typesPending } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });

  const service = (data?.data ?? []).find((s) => s.is_active !== false && !s.is_addon && s.offer_tag?.trim());
  const tag = service?.offer_tag?.trim();
  // Wait for vehicle types so a bike price never flashes up as the car headline.
  if (!service || !tag || typesPending) return null;

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

export function LaunchOfferStrip({ offer, onClaim }: { offer: PromotedOffer; onClaim: () => void }) {
  return (
    <button
      type="button"
      onClick={onClaim}
      className="group relative z-20 w-full overflow-hidden bg-[#080808] text-white shadow-[0_12px_28px_rgba(0,0,0,0.26)]"
    >
      <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(110deg,transparent_0%,rgba(255,255,255,0.10)_24%,transparent_45%)] animate-sheen-drift" />
      <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/35" />
      <div className="container-page relative flex min-h-[48px] items-center justify-center gap-2 overflow-hidden py-2 text-center sm:gap-3">
        <span className="hidden sm:inline-flex">
          <OfferTag label={offer.tag} className="ring-1 ring-white/40" />
        </span>
        <span className="min-w-0 truncate text-sm font-bold sm:text-base">{titleCase(offer.service.name)}</span>
        <span className="shrink-0 text-sm font-black sm:text-base">
          {!offer.samePriceForAllCars && <span className="mr-1 font-medium text-white/70">from</span>}
          {INR(offer.price)}
        </span>
        {offer.original != null && <span className="hidden shrink-0 text-sm text-white/50 line-through sm:inline">{INR(offer.original)}</span>}
        <DiscountBadge percent={discountPercent(offer.price, offer.original)} />
        <ArrowRight className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-1" />
      </div>
    </button>
  );
}

export function LaunchOfferPopup({ offer, onClaim }: { offer: PromotedOffer; onClaim: () => void }) {
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
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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
        <div className="fixed inset-0 z-[100000] flex items-center justify-center p-4">
          <motion.div
            className="absolute inset-0 bg-black/55 backdrop-blur-[3px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={close}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="offer-popup-title"
            initial={{ opacity: 0, y: 22, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.97 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
            className="relative z-10 w-full max-w-[560px] overflow-hidden rounded-[22px] border border-white/10 bg-[#080808] text-white shadow-[0_32px_100px_rgba(0,0,0,0.48)]"
          >
            <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(115deg,transparent_0%,rgba(255,255,255,0.13)_22%,transparent_44%)] animate-sheen-drift" />
            <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/35" />
            <button
              type="button"
              onClick={close}
              aria-label="Close offer"
              className="absolute right-3 top-3 z-20 flex h-9 w-9 items-center justify-center rounded-full bg-white/90 text-black shadow-sm transition-colors hover:bg-white"
            >
              <X className="h-5 w-5" />
            </button>

            <div className="relative grid gap-0 sm:grid-cols-[0.92fr_1.08fr]">
              <div className="relative min-h-[190px] overflow-hidden bg-[#111] sm:min-h-full">
                <img src={serviceImage(offer.service, 0)} alt={name} decoding="async" className="absolute inset-0 h-full w-full object-cover" />
                <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.08),rgba(0,0,0,0.45))]" />
              </div>

              <div className="p-5 sm:p-6">
                <OfferTag label={offer.tag} className="ring-1 ring-white/40" />

                <h2 id="offer-popup-title" className="mt-3 text-3xl font-black leading-tight text-white sm:text-[34px]">
                  {name}
                </h2>

                <div className="mt-3 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                  {!offer.samePriceForAllCars && <span className="text-sm text-white/70">From</span>}
                  <span className="text-[34px] font-black leading-none">{INR(offer.price)}</span>
                  {offer.original != null && <span className="text-base text-white/50 line-through">{INR(offer.original)}</span>}
                  <DiscountBadge percent={discountPercent(offer.price, offer.original)} />
                </div>

                {facts.length > 0 && (
                  <ul className="mt-4 space-y-1.5 text-sm text-white/80">
                    {facts.map((f) => (
                      <li key={f} className="flex items-center gap-2">
                        <Check className="h-4 w-4 shrink-0 text-white" strokeWidth={2.5} />
                        {f}
                      </li>
                    ))}
                  </ul>
                )}

                <button
                  type="button"
                  onClick={claim}
                  className="mt-5 flex w-full items-center justify-center gap-2 rounded-[12px] bg-[#E8A900] px-5 py-3.5 text-sm font-bold text-white transition-all hover:-translate-y-0.5 hover:bg-[#D99A00]"
                >
                  Book now
                  <ArrowRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
