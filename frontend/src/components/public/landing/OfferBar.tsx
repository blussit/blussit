import { useEffect, useState } from "react";
import { ArrowRight, X, Zap } from "lucide-react";
import type { PromotedOffer } from "../LaunchOfferPopup";
import { INR, titleCase } from "./shared";

const ROTATE_MS = 4000;
const CLOSED_KEY = "blussit:offerBarClosed";

const YELLOW = "#FCD116";
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
      className="relative flex w-full justify-center bg-transparent px-3 py-2 sm:py-3"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
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
        <button
          type="button"
          onClick={() => onBook(offer.service.slug)}
          aria-label={`Launch offer: ${name} at ${INR(offer.price)} — book now`}
          className="
            flex
            min-w-0
            items-center
            justify-center
            rounded-full
            px-1.5
            py-1.5
            transition-transform
            hover:scale-[1.01]
            active:scale-[0.99]
            sm:px-2
            sm:py-2
          "
        >
          {/* LAUNCH OFFER */}
          <span
            className="
              flex
              shrink-0
              items-center
              gap-1.5
              rounded-full
              px-3
              py-2
              text-[9px]
              font-extrabold
              uppercase
              tracking-[0.02em]
              text-[#111827]
              sm:px-4
              sm:py-2
              sm:text-[10px]
            "
            style={{ backgroundColor: YELLOW }}
          >
            <Zap
              className="h-3 w-3 sm:h-3.5 sm:w-3.5"
              fill="currentColor"
              strokeWidth={0}
            />

            <span className="whitespace-nowrap">
              LAUNCH OFFER 149 ONLY
            </span>
          </span>

          {/* DIVIDER */}
          <span className="mx-2 hidden h-6 w-px bg-gray-200 sm:block" />

          {/* SERVICE NAME */}
          <span
            className="
              hidden
              shrink-0
              whitespace-nowrap
              px-1
              text-[12px]
              font-semibold
              text-[#172033]
              sm:inline
              sm:text-[13px]
            "
          >
            {name}
          </span>

          <span className="mx-2 hidden h-6 w-px bg-gray-200 sm:block" />

          {/* PRICE */}
          <span className="flex shrink-0 items-baseline gap-1 px-1">
            <span
              className="
                whitespace-nowrap
                text-[15px]
                font-extrabold
                leading-none
                sm:text-[17px]
              "
              style={{ color: BLUE }}
            >
              {INR(offer.price)}
            </span>

            {hasMrp && (
              <span
                className="
                  whitespace-nowrap
                  text-[10px]
                  font-medium
                  text-gray-400
                  line-through
                  sm:text-[11px]
                "
              >
                {INR(offer.original!)}
              </span>
            )}
          </span>

          {/* DISCOUNT */}
          {off != null && (
            <>
              <span className="mx-2 hidden h-6 w-px bg-gray-200 sm:block" />

              <span
                className="
                  shrink-0
                  whitespace-nowrap
                  rounded-full
                  px-2
                  py-1
                  text-[9px]
                  font-extrabold
                  sm:px-2.5
                  sm:text-[10px]
                "
                style={{
                  backgroundColor: PINK,
                  color: PINK_TEXT,
                }}
              >
                {off}% OFF
              </span>
            </>
          )}

          {/* ARROW */}
          <span
            className="
              ml-2
              flex
              h-7
              w-7
              shrink-0
              items-center
              justify-center
              rounded-full
              sm:ml-2.5
              sm:h-8
              sm:w-8
            "
            style={{ backgroundColor: NAVY }}
          >
            <ArrowRight
              className="h-3.5 w-3.5 text-white sm:h-4 sm:w-4"
              strokeWidth={2.5}
            />
          </span>
        </button>

        {/* CLOSE */}
        <button
          type="button"
          onClick={close}
          aria-label="Close offer"
          className="
            absolute
            -right-1
            -top-1
            z-10
            flex
            h-5
            w-5
            items-center
            justify-center
            rounded-full
            bg-[#0E1A33]
            text-white
            opacity-0
            transition-opacity
            hover:opacity-100
            focus:opacity-100
          "
        >
          <X className="h-3 w-3" strokeWidth={2.5} />
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