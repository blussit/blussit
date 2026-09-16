import { useEffect, useState } from "react";
import { ArrowRight, Bike, CalendarDays, Sparkles, Timer, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import { couponApi } from "../../api/engagement";

const OFFER_END = new Date("2026-09-25T00:00:00+05:30").getTime();
const OFFER_CODE = "FREEBIKE";

function useOfferCountdown() {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const remaining = Math.max(0, OFFER_END - now);
  const days = Math.floor(remaining / 86_400_000);
  const hours = Math.floor((remaining % 86_400_000) / 3_600_000);
  const minutes = Math.floor((remaining % 3_600_000) / 60_000);
  return { days, hours, minutes };
}

function OfferCountdownPill({ compact = false }: { compact?: boolean }) {
  const { days, hours, minutes } = useOfferCountdown();
  return (
    <div className={`inline-flex min-w-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/95 text-[#111] shadow-[0_10px_28px_rgba(0,0,0,0.22)] ${compact ? "px-2 py-1 text-[11px]" : "gap-2 px-3 py-1.5 text-xs sm:text-sm"}`}>
      <Timer className="h-3.5 w-3.5 shrink-0 text-[#E8A900]" />
      {!compact && <span className="whitespace-nowrap font-bold">Offer Ends In</span>}
      <span className={`font-mono-num whitespace-nowrap rounded-full bg-[#111] font-black text-white ${compact ? "px-1.5 py-0.5 text-[9px]" : "px-2 py-0.5 text-[10px]"}`}>
        {days}D {hours}H {minutes}M
      </span>
    </div>
  );
}

export function LaunchOfferStrip({ onClaim }: { onClaim: () => void }) {
  return (
    <button
      type="button"
      onClick={onClaim}
      className="group relative z-20 w-full overflow-hidden bg-[#080808] text-white shadow-[0_12px_28px_rgba(0,0,0,0.26)]"
    >
      <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(110deg,transparent_0%,rgba(255,255,255,0.10)_24%,transparent_45%)] animate-sheen-drift" />
      <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/35" />
      <div className="container-page relative flex min-h-[52px] items-center justify-center gap-2 overflow-hidden py-2 text-center sm:gap-3">
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#E11D48] px-2.5 py-1 text-[11px] font-black uppercase tracking-wide text-white shadow-[0_0_18px_rgba(225,29,72,0.42)]">
          <Sparkles className="h-3.5 w-3.5" />
          Offer
        </span>
        <span className="shrink-0 text-sm font-black tracking-wide sm:text-base">Free Bike Wash</span>
        <span className="hidden shrink-0 text-sm font-semibold text-white/80 lg:inline">With Star Wash Or Deep Cleaning</span>
        <OfferCountdownPill compact />
        <ArrowRight className="h-4 w-4 shrink-0 transition-transform group-hover:translate-x-1" />
      </div>
    </button>
  );
}

export function LaunchOfferPopup({ onClaim }: { onClaim: () => void }) {
  const [open, setOpen] = useState(false);
  const dateOpen = Date.now() < OFFER_END;
  const { data: offer } = useQuery({
    queryKey: ["public-offer", OFFER_CODE],
    queryFn: () => couponApi.publicOffer(OFFER_CODE),
    enabled: dateOpen,
    retry: false,
  });

  useEffect(() => {
    if (dateOpen) setOpen(true);
  }, [dateOpen]);

  useEffect(() => {
    if (offer) setOpen(true);
  }, [offer]);

  const close = () => {
    setOpen(false);
  };

  const claim = () => {
    close();
    onClaim();
  };

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
          <div className="relative z-10 flex w-full max-w-[560px] flex-col items-center gap-3">
            <OfferCountdownPill />
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-labelledby="launch-offer-title"
              initial={{ opacity: 0, y: 22, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 18, scale: 0.97 }}
              transition={{ duration: 0.22, ease: "easeOut" }}
              className="relative w-full overflow-hidden rounded-[22px] border border-white/10 bg-[#080808] text-white shadow-[0_32px_100px_rgba(0,0,0,0.48)]"
            >
              <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(115deg,transparent_0%,rgba(255,255,255,0.13)_22%,transparent_44%)] animate-sheen-drift" />
              <span className="pointer-events-none absolute inset-x-0 top-0 h-px bg-white/35" />
              <button
                type="button"
                onClick={close}
                aria-label="Close offer"
                className="absolute right-3 top-3 z-20 flex h-9 w-9 items-center justify-center rounded-full bg-white/90 text-black shadow-sm transition-colors hover:bg-[#FFF4CD]"
              >
                <X className="h-5 w-5" />
              </button>

              <div className="relative grid gap-0 sm:grid-cols-[0.92fr_1.08fr]">
                <div className="relative min-h-[190px] overflow-hidden bg-[#111] sm:min-h-full">
                  <img src="/service-bike.webp" alt="Bike wash" className="absolute inset-0 h-full w-full object-cover" />
                  <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.12),rgba(0,0,0,0.58))]" />
                  <div className="absolute bottom-4 left-4 inline-flex items-center gap-2 rounded-full bg-[#E11D48] px-3 py-1.5 text-xs font-black uppercase tracking-wide text-white shadow-lg">
                    <Bike className="h-4 w-4" />
                    Free
                  </div>
                </div>

                <div className="p-5 sm:p-6">
                  <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/10 px-3 py-1.5 text-xs font-black uppercase tracking-wide text-[#FACC15]">
                    <Sparkles className="h-3.5 w-3.5" />
                    Launching Offer
                  </div>

                  <h2 id="launch-offer-title" className="text-3xl font-black leading-tight text-white sm:text-[34px]">
                    Get Free Bike Wash
                  </h2>

                  <p className="mt-3 text-sm leading-6 text-white/72">
                    Book Star Wash Or Deep Cleaning And Get One Bike Wash Added Free With Your Doorstep Visit.
                  </p>

                  <div className="mt-4 space-y-2 border-l-2 border-[#E8A900] pl-3">
                    <p className="text-sm font-semibold text-white">Valid With Star Wash Or Deep Cleaning.</p>
                    <p className="flex items-center gap-2 text-sm font-semibold text-white/70">
                      <CalendarDays className="h-4 w-4 shrink-0 text-[#E8A900]" />
                      Limited Time Offer Till 24 Sep.
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={claim}
                    className="mt-5 flex w-full items-center justify-center gap-2 rounded-[12px] bg-[#E8A900] px-5 py-3.5 text-sm font-black uppercase tracking-wide text-white transition-all hover:-translate-y-0.5 hover:bg-[#D99A00]"
                  >
                    Claim Offer And Book Now
                  </button>
                </div>
              </div>
            </motion.div>
          </div>
        </div>
      )}
    </AnimatePresence>
  );
}
