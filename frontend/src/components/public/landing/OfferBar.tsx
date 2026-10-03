import { useEffect, useState } from "react";
import { ArrowRight, X, Zap } from "lucide-react";
import type { PromotedOffer } from "../LaunchOfferPopup";
import { INR, titleCase } from "./shared";

const ROTATE_MS = 4000;
const CLOSED_KEY = "blussit:offerBarClosed";
// v2 design colours.
const BAR = "#0E1A28";
const YELLOW = "#FCD116";

const percentOff = (o: PromotedOffer) =>
  o.original && o.original > o.price ? Math.round(((o.original - o.price) / o.original) * 100) : null;

/** Slim bar under the navbar listing every live offer (Admin → Services →
 * offer tag), one at a time when there are several. Hidden when there are
 * none — or once the visitor closes it (for this browser session). */
export function OfferBar({ offers, onBook }: { offers: PromotedOffer[]; onBook: (slug?: string) => void }) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [closed, setClosed] = useState(() => {
    try {
      return sessionStorage.getItem(CLOSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (offers.length < 2 || paused) return;
    const t = window.setInterval(() => setIndex((i) => (i + 1) % offers.length), ROTATE_MS);
    return () => window.clearInterval(t);
  }, [offers.length, paused]);

  if (!offers.length || closed) return null;
  const offer = offers[index % offers.length];
  const off = percentOff(offer);
  const name = titleCase(offer.service.name);
  const hasMrp = offer.original != null && offer.original > offer.price;

  const close = () => {
    setClosed(true);
    try {
      sessionStorage.setItem(CLOSED_KEY, "1");
    } catch {
      /* storage blocked: closes for this page view */
    }
  };

  const pill = "shrink-0 items-center rounded-full font-extrabold uppercase text-[#0E1A33]";

  return (
    <div className="relative text-white" style={{ backgroundColor: BAR }} onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
      <button
        type="button"
        onClick={() => onBook(offer.service.slug)}
        className="container-page flex h-11 w-full items-center justify-center gap-2 sm:h-[52px] sm:gap-3.5"
        aria-label={`${offer.tag}: ${name} at ${INR(offer.price)} — book now`}
      >
        <span key={offer.service.id} className="flex min-w-0 items-center gap-2 animate-[offer-fade_.4s_ease] sm:gap-3.5">
          {/* Small phones: just the ⚡, so the wash name always fits. */}
          <span
            className={`${pill} inline-flex gap-1 p-1.5 text-[10px] tracking-wide min-[400px]:px-2.5 min-[400px]:py-1 sm:gap-1.5 sm:px-3.5 sm:py-1.5 sm:text-[13px]`}
            style={{ backgroundColor: YELLOW }}
            title={offer.tag}
          >
            <Zap className="h-3.5 w-3.5 sm:h-4 sm:w-4" fill="currentColor" strokeWidth={0} />
            <span className="hidden min-[400px]:inline">{offer.tag}</span>
          </span>

          {/* Phone: "Jet Wash ₹149 ₹299 →" */}
          <span className="flex min-w-0 items-baseline gap-1.5 sm:hidden">
            <span className="shrink-0 whitespace-nowrap text-[13px] font-semibold">{name}</span>
            <span className="shrink-0 text-[15px] font-extrabold" style={{ color: YELLOW }}>
              {INR(offer.price)}
            </span>
            {hasMrp && <span className="hidden shrink-0 text-[11.5px] text-white/50 line-through min-[340px]:inline">{INR(offer.original!)}</span>}
          </span>
          {off != null && (
            <span className={`${pill} inline-flex px-2 py-0.5 text-[10.5px] sm:hidden`} style={{ backgroundColor: YELLOW }}>
              {off}% OFF
            </span>
          )}

          {/* Tablet/desktop: "Jet Wash at ₹149  ₹299  [50% OFF]" */}
          <span className="hidden min-w-0 items-baseline gap-1.5 sm:flex">
            <span className="truncate text-[16px] font-semibold">
              {name} {offer.samePriceForAllCars ? "at" : "from"}
            </span>
            <span className="shrink-0 text-[22px] font-extrabold leading-none">{INR(offer.price)}</span>
          </span>
          {hasMrp && <span className="hidden shrink-0 text-[15px] text-white/50 line-through sm:inline">{INR(offer.original!)}</span>}
          {off != null && (
            <span className={`${pill} hidden px-3.5 py-1.5 text-[13px] sm:inline-flex`} style={{ backgroundColor: YELLOW }}>
              {off}% OFF
            </span>
          )}
        </span>

        <span className="flex shrink-0 items-center gap-1.5 text-[15px] font-bold">
          <span className="hidden sm:inline" style={{ color: YELLOW }}>
            Book Now
          </span>
          <ArrowRight className="h-4 w-4 text-white sm:hidden" strokeWidth={2.4} />
          <ArrowRight className="hidden h-[18px] w-[18px] sm:block" style={{ color: YELLOW }} strokeWidth={2.4} />
        </span>
      </button>

      <button
        type="button"
        onClick={close}
        aria-label="Close offer"
        className="absolute right-3 top-1/2 hidden h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full text-white/85 transition-colors hover:bg-white/10 hover:text-white sm:flex lg:right-5"
      >
        <X className="h-5 w-5" strokeWidth={2.2} />
      </button>

      {offers.length > 1 && (
        <div className="pointer-events-none absolute bottom-1 left-1/2 flex -translate-x-1/2 gap-1" aria-hidden="true">
          {offers.map((o, i) => (
            <span key={o.service.id} className={`h-[3px] rounded-full ${i === index % offers.length ? "w-3" : "w-1 bg-white/35"}`} style={i === index % offers.length ? { backgroundColor: YELLOW } : undefined} />
          ))}
        </div>
      )}
    </div>
  );
}
