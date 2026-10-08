import { useEffect, useState } from "react";
import { ArrowRight, X, Zap } from "lucide-react";
import type { PromotedOffer } from "../LaunchOfferPopup";
import { INR, titleCase } from "./shared";

const ROTATE_MS = 4000;
const CLOSED_KEY = "blussit:offerBarClosed";

const YELLOW = "#FFD21F";
const NAVY = "#0E1A33";
const BLUE = "#1769FF";
const PINK = "#FFE7E7";
const PINK_TEXT = "#E5484D";

const percentOff = (o: PromotedOffer) =>
  o.original && o.original > o.price
    ? Math.round(((o.original - o.price) / o.original) * 100)
    : null;

export function OfferBar({
  offers,
  onBook,
}: {
  offers: PromotedOffer[];
  onBook: (slug?: string) => void;
}) {
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

    const timer = window.setInterval(() => {
      setIndex((i) => (i + 1) % offers.length);
    }, ROTATE_MS);

    return () => window.clearInterval(timer);
  }, [offers.length, paused]);

  if (!offers.length || closed) return null;

  const offer = offers[index % offers.length];
  const off = percentOff(offer);

  const name = titleCase(offer.service.name);

  const hasMrp =
    offer.original != null && offer.original > offer.price;

  const close = () => {
    setClosed(true);

    try {
      sessionStorage.setItem(CLOSED_KEY, "1");
    } catch {
      // Storage blocked — closes for this page view.
    }
  };

  return (
    <div
      className="relative flex w-full justify-center bg-transparent px-2 py-2 min-[360px]:px-3 sm:py-3"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="relative w-fit max-w-full">
        <div
          className="
            relative
            flex
            w-fit
            max-w-full
            items-center
            overflow-hidden
            rounded-full
            border-2
            border-white
            bg-white
            shadow-[0_3px_14px_rgba(0,0,0,0.16)]
          "
        >
          {/* Every phone shows the whole offer: tag, service, price, MRP, % off.
              Only the word "Launch" and the dividers drop on the narrowest screens. */}
          <button
            type="button"
            onClick={() => onBook(offer.service.slug)}
            aria-label={`Launch offer: ${name} at ${INR(offer.price)} — book now`}
            className="flex min-w-0 items-center gap-1.5 rounded-full p-1 transition-transform min-[360px]:gap-2 min-[360px]:p-1.5 hover:scale-[1.01] active:scale-[0.99] sm:gap-2.5 sm:p-2"
          >
            <span
              className="flex shrink-0 items-center gap-1 rounded-full px-2 py-1.5 text-[10px] min-[360px]:px-2.5 font-extrabold uppercase tracking-[0.03em] text-[#111827] sm:gap-1.5 sm:px-4 sm:py-2 sm:text-[10px]"
              style={{ backgroundColor: YELLOW }}
            >
              <Zap className="h-3 w-3 sm:h-3.5 sm:w-3.5" fill="currentColor" strokeWidth={0} />
              <span className="whitespace-nowrap">
                <span className="hidden min-[388px]:inline">Launch </span>Offer
              </span>
            </span>

            <span className="min-w-0 truncate text-[12.5px] font-semibold text-[#172033] sm:text-[13px]">{name}</span>

            <span className="hidden h-6 w-px shrink-0 bg-gray-200 sm:block" />

            <span className="flex shrink-0 items-baseline gap-1">
              <span className="whitespace-nowrap text-[15px] font-extrabold leading-none sm:text-[17px]" style={{ color: BLUE }}>
                {INR(offer.price)}
              </span>
              {hasMrp && (
                <span className="whitespace-nowrap text-[10px] font-medium text-gray-400 line-through sm:text-[11px]">
                  {INR(offer.original!)}
                </span>
              )}
            </span>

            {off != null && (
              <>
                <span className="hidden h-6 w-px shrink-0 bg-gray-200 sm:block" />
                <span
                  className="shrink-0 whitespace-nowrap rounded-full px-1.5 py-1 text-[10px] font-extrabold sm:px-2.5 sm:text-[10px]"
                  style={{ backgroundColor: PINK, color: PINK_TEXT }}
                >
                  {off}% OFF
                </span>
              </>
            )}

            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full min-[360px]:h-7 min-[360px]:w-7 sm:h-8 sm:w-8" style={{ backgroundColor: NAVY }}>
              <ArrowRight className="h-3.5 w-3.5 text-white sm:h-4 sm:w-4" strokeWidth={2.5} />
            </span>
          </button>

        </div>

        {/* CLOSE — always visible (phones have no hover); hides it for this visit. */}
        <button
          type="button"
          onClick={close}
          aria-label="Close offer"
          className="absolute -right-1 -top-3.5 z-10 flex h-6 w-6 items-center justify-center rounded-full bg-[#0E1A33] text-white shadow-md ring-2 ring-white transition hover:bg-[#0A66F0]"
        >
          <X className="h-3 w-3" strokeWidth={3} />
        </button>
      </div>

      {/* ROTATION INDICATOR */}
      {offers.length > 1 && (
        <div
          className="
            pointer-events-none
            absolute
            bottom-0
            left-1/2
            flex
            -translate-x-1/2
            gap-1
          "
          aria-hidden="true"
        >
          {offers.map((o, i) => (
            <span
              key={o.service.id}
              className={`h-[2px] rounded-full ${
                i === index % offers.length ? "w-3" : "w-1 bg-gray-300"
              }`}
              style={
                i === index % offers.length
                  ? { backgroundColor: YELLOW }
                  : undefined
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}