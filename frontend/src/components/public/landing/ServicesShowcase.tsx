import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigationType, useNavigate } from "react-router-dom";
import { ArrowRight, ChevronLeft, ChevronRight, Clock, Sparkles, Droplet, Leaf, Armchair, ShieldCheck, Check } from "lucide-react";
import { useAuth } from "../../../context/AuthContext";
import { catalogApi, vehicleTypeApi } from "../../../api/catalog";
import type { Service, VehicleTypeOption } from "../../../types";
import {
  INR,
  groupServices,
  priceView,
  serviceImage,
  titleCase,
  vehicleLabel,
  parseIncludes,
  type ServiceGroup,
} from "./shared";

const LAST_SERVICE_KEY = "blussit:lastServiceCard";

export function ServicesShowcase({ limit, id, title, subtitle }: { limit?: number; id?: string; title?: string; subtitle?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const navType = useNavigationType();

  const all = (data?.data ?? []).filter((s) => s.is_active !== false);
  const groups = groupServices(all);
  const shown = limit ? groups.slice(0, limit) : groups;

  const scrollRef = useRef<HTMLDivElement>(null);

  // Use refs instead of state to avoid React re-renders breaking the 60fps animation
  const hoverRef = useRef(false);
  const touchRef = useRef(false);
  const manualRef = useRef(false);
  const manualTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  // We need 5 copies to ensure the user NEVER sees the end of the scroll width on ultra-wide screens
  const displayItems = [...shown, ...shown, ...shown, ...shown, ...shown];

  // Restoring Scroll Position Logic
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current || navType !== "POP" || !shown.length) return;
    let lastId: string | null = null;
    try {
      lastId = sessionStorage.getItem(LAST_SERVICE_KEY);
    } catch {
      // ignore
    }
    if (!lastId) return;
    const el = document.getElementById(`service-card-${lastId}`);
    if (el) {
      restoredRef.current = true;
      el.scrollIntoView({ behavior: "auto", inline: "center", block: "nearest" });
      
      // Temporarily pause animation on restore so the user sees where they came from
      manualRef.current = true;
      if (manualTimeout.current) clearTimeout(manualTimeout.current);
      manualTimeout.current = setTimeout(() => { manualRef.current = false; }, 2000);
    }
  }, [navType, shown.length]);

  // Infinite Scroll Engine (Bulletproof Floating Point approach)
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || shown.length === 0) return;
    
    let animationId: number;
    let lastTime = performance.now();
    const speed = 0.045; // ~45px per second
    const floatScroll = { current: 0 };
    
    let jumpDistance = 0;

    const initTimer = setTimeout(() => {
      if (!el || el.children.length < shown.length * 2) return;
      const card0 = el.children[0] as HTMLElement;
      const cardN = el.children[shown.length] as HTMLElement;
      if (!card0 || !cardN) return;
      
      // The exact pixel width of ONE complete set of original cards
      jumpDistance = cardN.offsetLeft - card0.offsetLeft;
      
      if (!restoredRef.current) {
        floatScroll.current = jumpDistance;
        el.scrollLeft = jumpDistance;
      } else {
        floatScroll.current = el.scrollLeft;
      }

      const loop = (time: number) => {
        let delta = time - lastTime;
        lastTime = time;
        
        // Cap delta at 30ms to prevent massive jumps when the browser tab is restored from background
        if (delta > 30) delta = 16.66; 

        const isPaused = hoverRef.current || touchRef.current || manualRef.current;

        if (jumpDistance > 0 && el) {
          if (!isPaused) {
            // We accumulate in a float to bypass browser Math.floor truncation on el.scrollLeft assignment
            floatScroll.current += speed * delta;
            
            // Core Infinite Loop Math
            if (floatScroll.current >= jumpDistance * 2) {
              floatScroll.current -= jumpDistance;
            } else if (floatScroll.current <= 0) {
              floatScroll.current += jumpDistance;
            }
            
            el.scrollLeft = floatScroll.current;
          } else {
            // User is interacting. We must sync the float back to the DOM so it picks up exactly where they left it
            floatScroll.current = el.scrollLeft;
            
            // Allow infinite loop wrap-around even when the user is manually dragging
            if (el.scrollLeft >= jumpDistance * 2) {
                el.scrollLeft -= jumpDistance;
                floatScroll.current -= jumpDistance;
            } else if (el.scrollLeft <= 0) {
                el.scrollLeft += jumpDistance;
                floatScroll.current += jumpDistance;
            }
          }
        }

        animationId = requestAnimationFrame(loop);
      };

      animationId = requestAnimationFrame(loop);
    }, 300); 

    return () => {
      clearTimeout(initTimer);
      cancelAnimationFrame(animationId);
    };
  }, [shown.length]);

  const handleManualScroll = (direction: 'prev' | 'next') => {
    manualRef.current = true;
    if (manualTimeout.current) clearTimeout(manualTimeout.current);
    manualTimeout.current = setTimeout(() => { manualRef.current = false; }, 800);

    if (!scrollRef.current) return;
    const cardWidth = scrollRef.current.firstElementChild?.clientWidth || 300;
    const scrollAmount = direction === 'next' ? (cardWidth + 20) : -(cardWidth + 20);
    scrollRef.current.scrollBy({ left: scrollAmount, behavior: "smooth" });
  };

  if (isLoading) return <SkeletonSection id={id} />;
  if (shown.length === 0) return null;

  return (
    <section id={id || "services"} className="relative w-full bg-white pt-4 pb-4 md:pt-6 md:pb-6 lg:pt-8 lg:pb-8 overflow-hidden">
      <div className="mx-auto w-[calc(100%-32px)] sm:w-[calc(100%-80px)] max-w-[1380px]">
        
        {/* Header */}
        <div className="mx-auto flex w-full flex-col sm:flex-row sm:items-end justify-between gap-5 sm:gap-8 px-1 lg:px-0">
          <div className="flex flex-col text-left lg:shrink-0">
            <div>
              <span className="inline-block rounded-full bg-[#EEF4FF] px-3 py-1 text-[11px] font-semibold uppercase tracking-[1.5px] text-[#1677FF]">
                {subtitle || "OUR SERVICES"}
              </span>
            </div>
            <h2 className="mt-3 font-display text-[28px] font-extrabold leading-[1.15] text-[#071A3D] sm:text-[34px] lg:text-[40px] lg:whitespace-nowrap">
              {title ? title : (
                <>
                  Choose the Right Care for{" "}
                  <span className="relative inline-block text-[#1677FF]">
                    Your Car.
                    <svg
                      viewBox="0 0 200 12"
                      preserveAspectRatio="none"
                      className="absolute -bottom-1.5 left-2 h-[8px] w-[75%]"
                      aria-hidden="true"
                    >
                      <path
                        d="M2,8 C50,2 120,2 198,7"
                        fill="none"
                        stroke="#FACC15"
                        strokeWidth="3"
                        strokeLinecap="round"
                      />
                    </svg>
                  </span>
                </>
              )}
            </h2>
            <p className="mt-[10px] text-[14px] leading-[1.5] text-[#64748B] sm:text-[15px]">
              Premium cleaning and detailing services, tailored for your car.
            </p>
          </div>
          
          <Link
            to="/services"
            className="inline-flex shrink-0 items-center gap-1.5 text-[14px] font-semibold text-[#1677FF] transition-colors hover:text-[#071A3D] sm:text-[15px] sm:pb-[6px]"
          >
            View All Services
            <ArrowRight className="h-4 w-4" strokeWidth={2.5} />
          </Link>
        </div>

        {/* Carousel Area */}
        <div 
          className="relative mt-6 lg:mt-8 w-full group/carousel"
          onMouseEnter={() => { hoverRef.current = true; }}
          onMouseLeave={() => { hoverRef.current = false; }}
          onTouchStart={() => { touchRef.current = true; }}
          onTouchEnd={() => { touchRef.current = false; }}
        >
          {/* Desktop Arrows */}
          <button 
            onClick={() => handleManualScroll('prev')} 
            className="hidden lg:flex absolute -left-5 xl:-left-6 top-1/2 z-10 h-[44px] w-[44px] -translate-y-1/2 items-center justify-center rounded-full border border-[#E5E5E5] bg-white text-[#071A3D] shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all hover:border-[#1677FF] hover:text-[#1677FF] active:scale-95"
            aria-label="Previous service"
          >
            <ChevronLeft className="h-5 w-5" strokeWidth={2} />
          </button>

          <button 
            onClick={() => handleManualScroll('next')} 
            className="hidden lg:flex absolute -right-5 xl:-right-6 top-1/2 z-10 h-[44px] w-[44px] -translate-y-1/2 items-center justify-center rounded-full border border-[#E5E5E5] bg-white text-[#071A3D] shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all hover:border-[#1677FF] hover:text-[#1677FF] active:scale-95"
            aria-label="Next service"
          >
            <ChevronRight className="h-5 w-5" strokeWidth={2} />
          </button>

          {/* Scroller */}
          <div 
            ref={scrollRef}
            className="flex w-full items-stretch gap-[16px] lg:gap-[20px] overflow-x-auto pb-[24px] pt-[8px] hide-scrollbar px-1 lg:px-0 cursor-grab active:cursor-grabbing"
            style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
          >
            {displayItems.map((g, i) => (
              <ServiceCard 
                key={`${g.primary.id}-${i}`} 
                group={g} 
                index={i % shown.length} 
                vehicleTypes={vehicleTypes} 
              />
            ))}
          </div>
        </div>
      </div>
    </section>
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
  const vehicle = vehicleLabel(s.vehicle_types, vehicleTypes) || "All Cars";
  const { user } = useAuth();
  const navigate = useNavigate();
  
  const bookHref = `${user?.role === "customer" ? "/app/book" : "/book"}?service=${encodeURIComponent(s.slug)}`;

  const handleBook = (e: React.MouseEvent) => {
    e.preventDefault();
    try {
      sessionStorage.setItem(LAST_SERVICE_KEY, s.id);
    } catch {
      // ignore
    }
    navigate(bookHref);
  };

  const { items } = parseIncludes(s.description);
  
  let features = items.slice(0, 3).map(text => {
    let Icon = Check;
    const lower = text.toLowerCase();
    if (lower.includes('waterless') || lower.includes('eco') || lower.includes('green')) Icon = Leaf;
    else if (lower.includes('exterior') || lower.includes('wash') || lower.includes('foam') || lower.includes('rinse')) Icon = Droplet;
    else if (lower.includes('interior') || lower.includes('vacuum') || lower.includes('seat') || lower.includes('mat')) Icon = Armchair;
    else if (lower.includes('polish') || lower.includes('shine') || lower.includes('wax') || lower.includes('tyre')) Icon = Sparkles;
    else if (lower.includes('protect') || lower.includes('coating')) Icon = ShieldCheck;
    return { text, Icon };
  });

  if (features.length === 0) {
    features = [
      { text: "Premium Exterior Cleaning", Icon: Droplet },
      { text: "Detailed Interior Vacuum", Icon: Armchair },
      { text: "Dashboard & Tyre Polish", Icon: Sparkles },
    ];
  }

  while (features.length < 3) {
    features.push({ text: "-", Icon: Check });
  }

  return (
    <div
      id={index === 0 ? `service-card-${s.id}` : undefined}
      className="group relative flex h-auto w-[calc(100vw-32px)] shrink-0 flex-col overflow-hidden rounded-[16px] lg:rounded-[18px] border border-[#E6E8EC] bg-white shadow-[0_6px_24px_rgba(15,30,60,0.06)] transition-all duration-300 hover:-translate-y-[3px] hover:shadow-[0_8px_30px_rgba(15,30,60,0.08)] sm:w-[calc(50%-10px)] lg:w-[calc(25%-15px)]"
    >
      {/* Image Area */}
      <div className="relative h-[220px] lg:h-[230px] w-full shrink-0 overflow-hidden bg-[#F8F9FA]">
        <img
          src={serviceImage(s, index)}
          alt={titleCase(s.name)}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 h-full w-full object-cover transition-transform duration-300 ease-out group-hover:scale-[1.02]"
        />
        
        {/* Duration Badge */}
        <div className="absolute left-4 top-4 flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 shadow-sm">
          <Clock className="h-[14px] w-[14px] text-[#071A3D]" strokeWidth={2.5} />
          <span className="text-[12px] font-semibold text-[#071A3D]">
            {s.duration_minutes || 45} MIN
          </span>
        </div>
        
        {pv.original && pv.original > pv.final && (
           <div className="absolute right-4 top-4 rounded-full bg-[#E11D48] px-2.5 py-1 text-[11px] font-bold text-white shadow-sm">
             {Math.round(((pv.original - pv.final) / pv.original) * 100)}% OFF
           </div>
        )}
      </div>

      {/* Content Area */}
      <div className="flex flex-1 flex-col p-[20px] lg:p-[22px]">
        <h3 className="text-[20px] lg:text-[22px] font-bold leading-snug text-[#071A3D]">
          {titleCase(s.name)}
        </h3>
        
        <p className="mt-1 mb-[16px] text-[14px] text-[#64748B]">
          For {vehicle}
        </p>

        {/* Features List */}
        <ul className="mb-[20px] flex flex-col gap-[8px] lg:gap-[10px]">
          {features.map((feat, idx) => (
            <li key={idx} className={`flex items-center gap-2.5 text-[13px] lg:text-[14px] leading-snug ${feat.text === "-" ? "invisible" : "text-[#475569]"}`}>
              <feat.Icon className="h-[14px] w-[14px] shrink-0 text-[#1677FF]" strokeWidth={1.5} />
              <span className="min-w-0 flex-1 truncate">{titleCase(feat.text)}</span>
            </li>
          ))}
        </ul>

        {/* Price & CTA pinned to bottom via mt-auto */}
        <div className="mt-auto flex items-end justify-between gap-3 border-t border-[#F0F0F0] pt-[16px]">
          <div className="flex flex-col">
            <span className="text-[12px] font-medium text-[#64748B] mb-0.5">From</span>
            <div className="flex items-baseline gap-1.5">
              <span className="text-[22px] lg:text-[24px] font-bold leading-none text-[#071A3D]">
                {INR(pv.final)}
              </span>
              {pv.original != null && (
                <span className="text-[12px] lg:text-[13px] font-medium text-[#94A3B8] line-through">
                  {INR(pv.original)}
                </span>
              )}
            </div>
          </div>

          <button
            onClick={handleBook}
            className="flex h-[38px] lg:h-[40px] items-center justify-center gap-1.5 rounded-[10px] lg:rounded-[12px] bg-[#FBBF24] px-[16px] lg:px-[18px] text-[14px] lg:text-[15px] font-semibold text-[#071A3D] transition-transform hover:-translate-y-0.5 active:translate-y-0"
          >
            Book Now
            <ArrowRight className="h-4 w-4" strokeWidth={2.5} />
          </button>
        </div>
      </div>
    </div>
  );
}

function SkeletonSection({ id }: { id?: string }) {
  return (
    <section id={id || "services"} className="relative w-full bg-white pt-[60px] pb-[10px] lg:pt-[70px] lg:pb-[10px] overflow-hidden">
      <div className="mx-auto w-[calc(100%-32px)] sm:w-[calc(100%-80px)] max-w-[1380px]">
        {/* Header Skeleton */}
        <div className="mx-auto flex w-full flex-col sm:flex-row sm:items-end justify-between gap-5 sm:gap-8 px-1 lg:px-0 animate-pulse">
          <div className="flex flex-col text-left">
            <div className="mb-2 h-3 w-24 rounded bg-[#E8E8E8]" />
            <div className="h-8 w-64 rounded bg-[#E8E8E8] sm:w-80" />
          </div>
          <div className="h-4 w-32 rounded bg-[#E8E8E8] pb-1" />
        </div>
        
        {/* Carousel Skeleton */}
        <div className="relative mt-[24px] lg:mt-[28px] w-full flex items-stretch gap-[16px] lg:gap-[20px] overflow-hidden px-1 lg:px-0">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="flex flex-col h-[520px] w-[calc(100vw-32px)] sm:w-[calc(50%-10px)] lg:w-[calc(25%-15px)] shrink-0 rounded-[16px] lg:rounded-[18px] border border-[#E6E8EC] bg-white animate-pulse">
              <div className="h-[220px] lg:h-[230px] w-full bg-[#F8F9FA]" />
              <div className="flex-1 p-[20px] lg:p-[22px]">
                 <div className="h-6 w-3/4 rounded bg-[#F1F5F9] mb-2" />
                 <div className="h-4 w-1/4 rounded bg-[#F1F5F9] mb-8" />
                 <div className="h-4 w-full rounded bg-[#F1F5F9] mb-3" />
                 <div className="h-4 w-full rounded bg-[#F1F5F9] mb-3" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
