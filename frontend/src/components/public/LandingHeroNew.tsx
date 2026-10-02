import { useEffect, useRef, useState } from "react";

import { Play, Car, Droplet, Star } from "lucide-react";
import { Sprout, MapPinHouse, BadgeCheck, ArrowRight } from "lucide-react";
import { WhatsAppFloatingButton } from "./WhatsAppFloatingButton";

function CountUp({ to, decimals = 0, duration = 1800 }: { to: number; decimals?: number; duration?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [value, setValue] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) {
      setValue(to);
      return;
    }
    let frame = 0;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        const start = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / duration);
          setValue(to * (1 - Math.pow(1 - t, 3)));
          if (t < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [to, duration]);

  return (
    <span ref={ref}>
      {value.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
    </span>
  );
}

export function LandingHero({ onBook }: { onBook: (serviceSlug?: string) => void }) {
  return (
    <section className="relative w-full bg-white overflow-hidden flex flex-col min-h-[90vh] lg:min-h-[85vh]">
      <WhatsAppFloatingButton />

      {/* ================================================== */}
      {/* DESKTOP HERO (Untouched)                           */}
      {/* ================================================== */}
      <div className="absolute inset-0 hidden md:block overflow-hidden bg-white">
        <img
          src="/wash-image1.png"
          alt="BLUSSIT Car Wash"
          className="absolute inset-0 h-full w-full object-cover object-right"
        />
          <div
            className="absolute inset-0 w-full h-full"
            style={{
              background: "linear-gradient(90deg, rgba(255,255,255,0.85) 0%, rgba(255,255,255,0.6) 15%, rgba(255,255,255,0) 30%)",
              pointerEvents: "none"
            }}
          />
      </div>

      {/* DESKTOP OFFER PILL */}
      <div className="hidden md:flex relative z-20 md:absolute md:top-2 md:mt-0 md:mb-0 md:left-1/2 md:-translate-x-1/2 md:w-auto">
        <OfferPill onBook={onBook} />
      </div>

      {/* DESKTOP CONTENT CONTAINER */}
      <div className="hidden md:flex relative mx-auto w-full max-w-[1920px] flex-1 flex-col justify-center px-5 py-6 md:px-8 lg:px-[4%] xl:px-[3%] md:pt-12 md:pb-8 z-10">
        
        <div className="relative flex flex-col w-full md:w-[45%] lg:w-[42%] xl:w-[40%]">
          


          <span className="mb-2 inline-flex w-fit items-center rounded-full bg-[#E6F0FF] px-3.5 py-1.5 text-[10.5px] font-bold tracking-wide text-[#1677FF] shadow-sm md:mb-3">
            PREMIUM DOORSTEP CAR CARE
          </span>

          <h1 className="mb-3 max-w-[540px] text-[40px] font-bold leading-[1.05] tracking-[-0.03em] text-[#071A3D] md:mb-4 md:text-[64px] lg:text-[68px] md:leading-[0.98]">
            Your Car.<br />
            Washed at<br />
            <span className="text-[#1677FF]">Your Doorstep.</span>
          </h1>

          <div className="mb-4 flex w-full items-start justify-between md:mb-5 md:justify-start md:gap-4 lg:gap-5">
            <FeatureItem
              icon={
                <div className="flex h-[44px] w-[44px] md:h-[48px] md:w-[48px] items-center justify-center rounded-full bg-[#EAF3FF]">
                  <Sprout className="text-[#16A34A] h-[20px] w-[20px] md:h-[22px] md:w-[22px]" strokeWidth={2} />
                </div>
              }
              text={<>Waterless<br />Options</>}
            />
            <FeatureItem
              icon={
                <div className="flex h-[44px] w-[44px] md:h-[48px] md:w-[48px] items-center justify-center rounded-full bg-[#EAF3FF]">
                  <MapPinHouse className="text-[#1677FF] h-[20px] w-[20px] md:h-[22px] md:w-[22px]" strokeWidth={2} />
                </div>
              }
              text={<>At Your<br />Doorstep</>}
            />
            <FeatureItem
              icon={
                <div className="flex h-[44px] w-[44px] md:h-[48px] md:w-[48px] items-center justify-center rounded-full bg-[#EAF3FF]">
                  <BadgeCheck className="text-[#1677FF] h-[20px] w-[20px] md:h-[22px] md:w-[22px]" strokeWidth={2} />
                </div>
              }
              text={<>Trusted<br />Professionals</>}
            />
          </div>

          <div className="mb-5 flex flex-col gap-3 md:mb-5 md:flex-row md:items-center md:gap-3">
            <button
              onClick={() => onBook()}
              className="flex h-[46px] w-full items-center justify-center gap-2 rounded-full bg-[#FBBF24] px-6 text-[14px] font-bold text-[#071A3D] transition-transform hover:-translate-y-0.5 active:translate-y-0 md:h-[48px] md:w-[180px] lg:w-[190px]"
            >
              Book a Wash
              <ArrowRight className="text-[#071A3D] h-[14px] w-[14px]" strokeWidth={2.5} />
            </button>
            <button
              onClick={() => document.getElementById("how-it-works")?.scrollIntoView({ behavior: "smooth" })}
              className="flex h-[46px] w-full items-center justify-center gap-2 rounded-full border border-[#1677FF]/30 bg-white px-6 text-[14px] font-bold text-[#1677FF] transition-all hover:-translate-y-0.5 hover:border-[#1677FF]/50 hover:bg-[#F5F9FF] active:translate-y-0 md:h-[48px] md:w-[180px] lg:w-[190px]"
            >
            <Play className="text-[#1677FF] h-[14px] w-[14px]" fill="currentColor" strokeWidth={0} />
              How It Works
            </button>
          </div>

          <div className="flex h-[56px] w-full items-center justify-between divide-x divide-black/[0.04] rounded-full border border-black/5 bg-white px-2 shadow-[0_4px_16px_rgba(0,0,0,0.04)] md:border-white/50 md:bg-white/50 md:shadow-[0_2px_12px_rgba(0,0,0,0.03)] md:backdrop-blur-md md:h-[60px] md:w-fit md:self-start md:px-3">
            <TrustItem 
              icon={<Car className="text-[#1677FF] h-[18px] w-[18px]" strokeWidth={2} />} 
              countTo={140}
              suffix="+"
              label="SERVICES DONE" 
            />
            <TrustItem 
              icon={<Droplet className="text-[#1677FF] h-[18px] w-[18px]" strokeWidth={2} />} 
              countTo={2000}
              suffix="+"
              label="LITERS OF WATER SAVED" 
            />
            <TrustItem 
              icon={<Star className="text-[#FBBF24] h-[18px] w-[18px]" fill="currentColor" strokeWidth={0} />} 
              countTo={4.9}
              decimals={1}
              suffix="/5"
              label="CUSTOMER RATING" 
            />
          </div>
        </div>
      </div>

      {/* ================================================== */}
      {/* MOBILE HERO (Completely isolated)                    */}
      {/* ================================================== */}
      <div className="flex flex-col w-full md:hidden bg-white overflow-hidden">
        
        {/* ONE CONTINUOUS HERO VISUAL */}
        <div 
          className="relative w-full flex flex-col pt-4 min-h-[165vw] sm:min-h-[700px] bg-cover bg-bottom bg-no-repeat"
          style={{ backgroundImage: "url('/wash-image-mobile.png?v=2')" }}
        >
          {/* Extremely subtle transparent gradient just to ensure white text or small contrast issues are resolved, NOT a white box. */}
          <div className="absolute inset-0 bg-gradient-to-b from-white/50 via-white/5 to-transparent pointer-events-none" />

          {/* Foreground Content */}
          <div className="relative z-10 flex flex-col w-full px-5">
            
            {/* 1. Launch Offer */}
            <div className="flex w-full justify-center pb-[20px]">
              <MobileOfferPill onBook={onBook} />
            </div>

            {/* 2. Badge */}
            <span className="mb-[14px] inline-flex w-fit items-center rounded-full bg-white/95 backdrop-blur-sm px-[16px] py-[10px] text-[12px] font-bold tracking-wide text-[#1677FF] shadow-sm">
              PREMIUM DOORSTEP CAR CARE
            </span>
            
            {/* 3. Heading */}
            <h1 className="mb-[24px] max-w-[320px] font-[800] leading-[0.98] tracking-[-0.03em] text-[#071A3D]" style={{ fontSize: 'clamp(44px, 11vw, 50px)' }}>
              Your Car.<br />
              Washed at<br />
              <span className="text-[#1677FF] whitespace-nowrap">Your Doorstep.</span>
            </h1>
            
            {/* 4. Features Row */}
            <div className="flex w-full items-start justify-between">
              <div className="flex flex-col items-center gap-2 text-center flex-1">
                <Sprout className="h-[26px] w-[26px] text-[#16A34A] drop-shadow-sm" strokeWidth={2} />
                <span className="text-[12px] font-[600] leading-tight text-[#071A3D] drop-shadow-md">Waterless<br />Options</span>
              </div>
              <div className="flex flex-col items-center gap-2 text-center flex-1">
                <MapPinHouse className="h-[26px] w-[26px] text-[#1677FF] drop-shadow-sm" strokeWidth={2} />
                <span className="text-[12px] font-[600] leading-tight text-[#071A3D] drop-shadow-md">At Your<br />Doorstep</span>
              </div>
              <div className="flex flex-col items-center gap-2 text-center flex-1">
                <BadgeCheck className="h-[26px] w-[26px] text-[#1677FF] drop-shadow-sm" strokeWidth={2} />
                <span className="text-[12px] font-[600] leading-tight text-[#071A3D] drop-shadow-md">Trusted<br />Professionals</span>
              </div>
            </div>

          </div>
        </div>

        {/* 5. CTA Buttons & Stats */}
        <div className="flex flex-col px-5 pt-[20px] pb-8 w-full bg-white relative z-20">
          <button
            onClick={() => onBook()}
            className="mb-[12px] flex h-[56px] w-full items-center justify-center gap-2 rounded-full bg-[#FBBF24] px-6 text-[15px] font-[700] text-[#071A3D] transition-transform active:scale-[0.98]"
          >
            Book a Wash
            <ArrowRight className="h-[18px] w-[18px]" strokeWidth={2.5} />
          </button>
          <button
            onClick={() => document.getElementById("how-it-works")?.scrollIntoView({ behavior: "smooth" })}
            className="mb-[20px] flex h-[56px] w-full items-center justify-center gap-2 rounded-full border border-[#1677FF] bg-white px-6 text-[15px] font-[700] text-[#1677FF] transition-all active:scale-[0.98] active:bg-[#F5F9FF]"
          >
            <Play className="text-[#1677FF] h-[14px] w-[14px]" fill="currentColor" strokeWidth={0} />
            How It Works
          </button>

          <div data-hero-stats className="mt-3 flex w-full items-center justify-between divide-x divide-black/[0.04] rounded-2xl border border-black/5 bg-white py-3 shadow-[0_4px_16px_rgba(0,0,0,0.04)]">
            <TrustItem 
              icon={<Car className="text-[#1677FF] h-[20px] w-[20px]" strokeWidth={2} />} 
              countTo={140}
              suffix="+"
              label="SERVICES DONE" 
            />
            <TrustItem 
              icon={<Droplet className="text-[#1677FF] h-[20px] w-[20px]" strokeWidth={2} />} 
              countTo={2000}
              suffix="+"
              label="LITERS SAVED" 
            />
            <TrustItem 
              icon={<Star className="text-[#FBBF24] h-[20px] w-[20px]" fill="currentColor" strokeWidth={0} />} 
              countTo={4.9}
              decimals={1}
              suffix="/5"
              label="RATING" 
            />
          </div>
        </div>

      </div>
    </section>
  );
}

function OfferPill({ onBook }: { onBook: (serviceSlug?: string) => void }) {
  return (
    <button
      onClick={() => onBook('jet-wash')}
      className="group flex w-full max-w-max items-center rounded-full border border-black/5 bg-white p-1 pr-1 shadow-[0_4px_20px_rgba(0,0,0,0.06)] transition-transform hover:-translate-y-0.5 h-[42px] md:h-[48px] mx-auto md:mx-0"
    >
      <div className="flex h-full items-center gap-1.5 rounded-full bg-[#FBBF24] px-2.5 md:px-3">
        <span className="text-[10px] md:text-[12px]">⚡</span>
        <span className="text-[8px] md:text-[9.5px] font-black uppercase tracking-wide text-[#071A3D] whitespace-nowrap">
          LAUNCH OFFER 149 ONLY
        </span>
      </div>
      
      <div className="mx-1.5 md:mx-2.5 h-3.5 md:h-4 w-px bg-black/10 shrink-0" />
      
      <div className="flex items-center gap-1.5 md:gap-2.5">
        <span className="text-[9px] md:text-[11px] font-bold text-[#071A3D] whitespace-nowrap">Jet Wash</span>
        
        <div className="h-3 md:h-3.5 w-px bg-black/10 shrink-0" />
        
        <div className="flex items-center gap-1 md:gap-1.5">
          <span className="text-[10px] md:text-[12px] font-black text-[#1677FF] whitespace-nowrap">₹149</span>
          <span className="text-[8px] md:text-[10px] font-medium text-black/40 line-through whitespace-nowrap">₹299</span>
        </div>

        <div className="h-3 md:h-3.5 w-px bg-black/10 shrink-0" />
        
        <span className="rounded-full bg-[#FFF0F2] px-1.5 py-0.5 text-[6.5px] md:text-[8px] font-black text-[#E11D48] whitespace-nowrap">
          50% OFF
        </span>
      </div>
        
      <div className="ml-1.5 md:ml-2.5 flex h-6 w-6 md:h-8 md:w-8 shrink-0 items-center justify-center rounded-full bg-[#071A3D] text-white shadow-sm transition-transform group-hover:scale-105">
        <ArrowRight className="h-[12px] w-[12px] md:h-[14px] md:w-[14px]" strokeWidth={2.5} />
      </div>
    </button>
  );
}

function MobileOfferPill({ onBook }: { onBook: (serviceSlug?: string) => void }) {
  return (
    <button
      onClick={() => onBook('jet-wash')}
      className="group flex w-full max-w-max items-center rounded-full border border-black/5 bg-white p-1 pr-1 shadow-[0_4px_20px_rgba(0,0,0,0.06)] active:scale-[0.98] h-[46px]"
    >
      <div className="flex flex-col h-full justify-center items-center rounded-full bg-[#FBBF24] px-3">
        <span className="text-[7.5px] font-black uppercase tracking-wide text-[#071A3D] leading-tight">
          LAUNCH OFFER
        </span>
        <span className="text-[9px] font-black uppercase tracking-wide text-[#071A3D] leading-tight">
          ₹149 ONLY
        </span>
      </div>
      
      <div className="mx-2 h-4 w-px bg-black/10 shrink-0" />
      
      <div className="flex items-center gap-1.5">
        <span className="text-[11px] font-bold text-[#071A3D] whitespace-nowrap">Jet Wash</span>
        
        <div className="h-3.5 w-px bg-black/10 shrink-0" />
        
        <div className="flex items-center gap-1">
          <span className="text-[12px] font-black text-[#1677FF] whitespace-nowrap">₹149</span>
          <span className="text-[9px] font-medium text-black/40 line-through whitespace-nowrap">₹299</span>
        </div>

        <div className="h-3.5 w-px bg-black/10 shrink-0" />
        
        <span className="rounded-full bg-[#FFF0F2] px-1.5 py-0.5 text-[8.5px] font-black text-[#E11D48] whitespace-nowrap">
          50% OFF
        </span>
      </div>
        
      <div className="ml-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#071A3D] text-white shadow-sm transition-transform group-hover:scale-105">
        <ArrowRight className="h-[14px] w-[14px]" strokeWidth={2.5} />
      </div>
    </button>
  );
}

function FeatureItem({ icon, text }: { icon: React.ReactNode; text: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1.5 text-center md:flex-row md:text-left">
      {icon}
      <span className="text-[11px] font-semibold leading-tight text-[#071A3D] md:text-[13.5px]">
        {text}
      </span>
    </div>
  );
}

function TrustItem({ icon, countTo, decimals = 0, suffix, label }: { icon: React.ReactNode; countTo: number; decimals?: number; suffix: string; label: string }) {
  return (
    <div className="flex flex-col flex-1 items-center justify-center gap-1.5 px-1.5 py-1 text-center md:flex-row md:flex-initial md:justify-start md:gap-2.5 md:px-4 md:py-1 md:text-left">
      {icon}
      <div className="flex flex-col">
        <span className="text-[13px] font-bold leading-none text-[#1677FF] md:text-[15px]">
          <CountUp to={countTo} decimals={decimals} />{suffix}
        </span>
        <span className="mt-[3px] text-[7.5px] font-bold tracking-wider text-[#071A3D]/60 md:text-[8px]">{label}</span>
      </div>
    </div>
  );
}
