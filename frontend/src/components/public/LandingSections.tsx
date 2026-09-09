import { type ReactNode, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowRight,
  BadgeCheck,
  Calendar,
  Car,
  Clock,
  Droplet,
  IndianRupee,
  Leaf,
  MapPin,
  PlayCircle,
  ShieldCheck,
  Sparkles,
  Star,
  UserCheck,
  Truck,
} from "lucide-react";
import { catalogApi, homepageConfigApi } from "../../api/catalog";
import { WhatsAppFloatingButton } from "./WhatsAppFloatingButton";

/* ------------------------------------------------------------------ */
/* Images                                                             */
/* ------------------------------------------------------------------ */

const IMG = {
  hero: "/hero-image.webp",
  whyChoose: "/why-choose.webp",
  howItWorks: "/how-works.webp",

  premium:
     "/banner.webp",

  plans:
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=1200&auto=format&fit=crop",

  services: [
    "/service-1.webp",
    "/service-2.webp",
    "/service-3.webp",
    "/service-4.webp",
    "/service-5.webp",
   "/service-6.webp",
    "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?q=80&w=800&auto=format&fit=crop",
  ],

  videos: [
    "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1601362840469-51e4d8d58785?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1493238792000-8113da705763?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=600&auto=format&fit=crop",
  ],
};

/* Real Blussit shoot photos, matched to the service by NAME (the old
   behavior cycled images by index, so e.g. Bike Wash could show a car).
   Anything without a match keeps the index-cycled fallback. */
const SERVICE_IMAGE_MAP: [RegExp, string][] = [
  [/waterless/i, "/service-waterless.webp"],
  [/deep/i, "/service-deepclean.webp"],
  [/jet/i, "/service-jet.webp"],
  [/bike/i, "/service-bike.webp"],
];

function serviceImageFor(name: string | undefined, index: number): string {
  const hit = name ? SERVICE_IMAGE_MAP.find(([re]) => re.test(name)) : undefined;
  return hit ? hit[1] : IMG.services[index % IMG.services.length];
}

/* ------------------------------------------------------------------ */
/* Buttons                                                            */
/* ------------------------------------------------------------------ */

function PrimaryButton({
  children,
  onClick,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group inline-flex cursor-pointer items-center justify-center gap-2 rounded-[10px] bg-[#E8A900] px-6 py-3 text-sm font-bold tracking-wide text-white shadow-[0_6px_16px_rgba(232,169,0,0.24)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#D99A00] hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)] active:translate-y-0 active:shadow-md ${className}`}
    >
      {children}
    </button>
  );
}

function OutlineButton({
  children,
  onClick,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group inline-flex cursor-pointer items-center justify-center gap-2 rounded-[10px] border border-white/55 bg-[#141414]/35 px-6 py-3 text-sm font-bold tracking-wide text-white shadow-sm backdrop-blur-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-[#E8A900] hover:bg-[#E8A900]/15 hover:text-white active:translate-y-0 ${className}`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Offer Ticker                                                       */
/* ------------------------------------------------------------------ */

export function OfferTicker() {
  return null;
}

/* ------------------------------------------------------------------ */
/* HERO SLIDES                                                        */
/* ------------------------------------------------------------------ */

const HERO_SLIDES = [
  {
    id: "home",
    type: "home" as const,
    eyebrow: "Premium Car Care",
    title: "CAR WASH",
    titleLine2: "AT YOUR",
    titleAccent: "DOORSTEP",
    description:
      "We come to you. You relax.\nWe make your car shine like new.",
    image: "/hero-image.webp",
    serviceSlug: "",
  },

  // Offer slides mirror the live catalogue (backend/app/seed.py) — prices
  // are the hatchback price; the wizard shows the exact per-type amount.
  {
    id: "star-wash",
    type: "offer" as const,
    eyebrow: "Launch Offer",
    title: "STAR WASH",
    titleLine2: "",
    titleAccent: "",
    description:
      "Foam wash outside, vacuum and dashboard polish inside.",
    price: "₹349",
    oldPrice: "₹449",
    discount: "SAVE 22%",
    tag: "MOST POPULAR",
    note: "",
    items: [
      "Exterior foam wash",
      "Interior vacuum",
      "Dashboard polish",
    ],
    image: "/service-star.webp",
    serviceSlug: "star-wash",
  },

  {
    id: "deep-cleaning",
    type: "offer" as const,
    eyebrow: "Launch Offer",
    title: "DEEP CLEANING",
    titleLine2: "",
    titleAccent: "",
    description:
      "Everything in Star Wash, plus a full interior clean.",
    price: "₹699",
    oldPrice: "₹999",
    discount: "SAVE 30%",
    tag: "BEST VALUE",
    note: "",
    items: [
      "Everything in Star Wash",
      "Seat cleaning",
      "Floor & mats cleaning",
      "Pedal & door cleaning",
    ],
    image: "/hero-img3.webp",
    serviceSlug: "deep-cleaning",
  },

  {
    id: "waterless",
    type: "offer" as const,
    eyebrow: "Launch Offer",
    title: "WATERLESS",
    titleLine2: "SERVICE",
    titleAccent: "",
    description:
      "A clean finish without a drop of water wasted.",
    price: "₹319",
    oldPrice: "₹399",
    discount: "SAVE 20%",
    tag: "ECO FRIENDLY",
    note: "",
    items: [
      "Waterless exterior clean",
      "Interior vacuum",
      "Dashboard polish",
    ],
    image: "/hero-img4.webp",
    serviceSlug: "waterless-service",
  },

  {
    id: "jet-wash",
    type: "offer" as const,
    eyebrow: "Launch Offer",
    title: "JET WASH",
    titleLine2: "",
    titleAccent: "",
    description:
      "A quick exterior refresh for your everyday drive.",
    price: "₹249",
    oldPrice: "₹299",
    discount: "SAVE 17%",
    tag: "QUICK CLEAN",
    note: "",
    items: [
      "Exterior foam wash",
      "Tyre polish",
    ],
    image: "/service-jet.webp",
    serviceSlug: "jet-wash",
  },

  {
    id: "bike-wash",
    type: "offer" as const,
    eyebrow: "Two-Wheelers",
    title: "BIKE",
    titleLine2: "WASH",
    titleAccent: "",
    description:
      "Foam wash for your bike at your doorstep.\nTwo bikes together for ₹159.",
    price: "₹99",
    oldPrice: "",
    discount: "1 BIKE",
    tag: "BIKE",
    note: "",
    items: [
      "Bike foam wash",
      "Add polish for ₹30",
      "Add a bike to any car wash for ₹60",
    ],
    image: "/service-bike.webp",
    serviceSlug: "bike-wash",
  },
];

/* ------------------------------------------------------------------ */
/* HERO FEATURES                                                      */
/* ------------------------------------------------------------------ */

const HERO_FEATURES = [
  {
    icon: Droplet,
    label: "Water Efficient",
    sub: "Save Water",
  },
  {
    icon: ShieldCheck,
    label: "Safe & Secure",
    sub: "100% Safe Wash",
  },
  {
    icon: UserCheck,
    label: "Trained Experts",
    sub: "Verified Staff",
  },
  {
    icon: Leaf,
    label: "Eco Friendly",
    sub: "Green Products",
  },
];

/* ------------------------------------------------------------------ */
/* FIGMA OFFERS                                                       */
/* ------------------------------------------------------------------ */

const FIGMA_OFFERS = [
  {
    id: 1,
    badge: "BEST DEAL",
    label: "",
    value: "20%",
    suffix: "OFF",
    description: "Deep clean. Premium shine.",
    subDescription: "Make your car look showroom fresh.",
    button: "Book Now",
    image:
      "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?q=85&w=900&auto=format&fit=crop",
    theme: "gold",
  },

  {
    id: 2,
    badge: "POPULAR",
    label: "",
    value: "15%",
    suffix: "OFF",
    description: "Inside + outside care.",
    subDescription: "A complete refresh for your ride.",
    button: "Book Now",
    image:
      "https://images.unsplash.com/photo-1502877338535-766e1452684a?q=85&w=900&auto=format&fit=crop",
    theme: "white",
  },

  {
    id: 3,
    badge: "LIMITED",
    label: "",
    value: "10%",
    suffix: "OFF",
    description: "Your car deserves regular care.",
    subDescription: "Save more with every monthly wash.",
    button: "Book Now",
    image:
      "https://images.unsplash.com/photo-1554224155-8d04cb21cd6c?q=85&w=900&auto=format&fit=crop",
    theme: "gold",
  },

  {
    id: 4,
    badge: "NEW USER",
    label: "",
    value: "₹100",
    suffix: "OFF",
    description: "New to BLUSSIT? Start fresh.",
    subDescription: "Get ₹100 off your first doorstep wash.",
    button: "Book Now",
    image:
      "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTufGY7O8JdzNN9oznhqBSaEiW6-gubnF_FsrUDy8iECA&s=100",
    theme: "white",
  },
];

/* ------------------------------------------------------------------ */
/* LANDING HERO                                                       */
/* ------------------------------------------------------------------ */

function useCarouselSwipe(length: number, interval = 4000) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const touchStartX = useRef<number | null>(null);

  useEffect(() => {
    if (isPaused) return;
    const timer = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % length);
    }, interval);
    return () => clearInterval(timer);
  }, [isPaused, length, interval]);

  const handleStart = (clientX: number) => {
    setIsPaused(true);
    touchStartX.current = clientX;
  };
  const handleEnd = (clientX: number) => {
    setIsPaused(false);
    if (touchStartX.current === null) return;
    const distance = touchStartX.current - clientX;
    if (distance > 50) {
      setActiveIndex((prev) => (prev + 1) % length);
    } else if (distance < -50) {
      setActiveIndex((prev) => (prev - 1 + length) % length);
    }
    touchStartX.current = null;
  };

  const handlers = {
    onTouchStart: (e: React.TouchEvent) => handleStart(e.touches[0].clientX),
    onTouchEnd: (e: React.TouchEvent) => handleEnd(e.changedTouches[0].clientX),
    onMouseDown: (e: React.MouseEvent) => handleStart(e.clientX),
    onMouseUp: (e: React.MouseEvent) => handleEnd(e.clientX),
    onMouseLeave: () => {
      setIsPaused(false);
      touchStartX.current = null;
    }
  };

  return { activeIndex, setActiveIndex, handlers };
}

function useContinuousMarquee(speed = 45) {
  const trackRef = useRef<HTMLDivElement>(null);
  const setRef = useRef<HTMLDivElement>(null);
  
  const state = useRef({
    offset: 0,
    isDragging: false,
    startX: 0,
    lastTime: performance.now(),
    setWidth: 0,
    animationFrame: 0,
  });

  useEffect(() => {
    const track = trackRef.current;
    const firstSet = setRef.current;
    if (!track || !firstSet) return;

    const measure = () => {
      state.current.setWidth = firstSet.getBoundingClientRect().width;
    };
    
    const observer = new ResizeObserver(measure);
    observer.observe(firstSet);
    measure();

    const animate = (now: number) => {
      const delta = Math.min(now - state.current.lastTime, 50);
      state.current.lastTime = now;

      if (!state.current.isDragging && state.current.setWidth > 0) {
        state.current.offset += (speed * delta) / 1000;
        
        if (state.current.offset >= state.current.setWidth) {
          state.current.offset %= state.current.setWidth;
        } else if (state.current.offset < 0) {
          state.current.offset = (state.current.offset % state.current.setWidth) + state.current.setWidth;
        }
        
        track.style.transform = `translate3d(-${state.current.offset}px, 0, 0)`;
      }
      
      state.current.animationFrame = requestAnimationFrame(animate);
    };

    state.current.lastTime = performance.now();
    state.current.animationFrame = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(state.current.animationFrame);
      observer.disconnect();
    };
  }, [speed]);

  const onPointerDown = (e: React.PointerEvent | React.TouchEvent | React.MouseEvent) => {
    state.current.isDragging = true;
    state.current.startX = 'touches' in e ? e.touches[0].clientX : (e as React.MouseEvent).clientX;
  };

  const onPointerMove = (e: React.PointerEvent | React.TouchEvent | React.MouseEvent) => {
    if (!state.current.isDragging) return;
    
    const currentX = 'touches' in e ? e.touches[0].clientX : (e as React.MouseEvent).clientX;
    const deltaX = state.current.startX - currentX;
    state.current.startX = currentX;
    
    state.current.offset += deltaX;
    
    if (state.current.setWidth > 0) {
      if (state.current.offset >= state.current.setWidth) {
        state.current.offset %= state.current.setWidth;
      } else if (state.current.offset < 0) {
        state.current.offset = (state.current.offset % state.current.setWidth) + state.current.setWidth;
      }
    }
    
    if (trackRef.current) {
      trackRef.current.style.transform = `translate3d(-${state.current.offset}px, 0, 0)`;
    }
  };

  const onPointerUp = () => {
    if (!state.current.isDragging) return;
    state.current.isDragging = false;
    state.current.lastTime = performance.now();
  };

  const handlers = {
    onTouchStart: onPointerDown,
    onTouchMove: onPointerMove,
    onTouchEnd: onPointerUp,
    onTouchCancel: onPointerUp,
    onMouseDown: onPointerDown,
    onMouseMove: onPointerMove,
    onMouseUp: onPointerUp,
    onMouseLeave: onPointerUp,
  };

  return { trackRef, setRef, handlers };
}

/** The "Limited Time Offers" marquee under the hero. Parked for now —
 * flip to true to bring it back without touching the markup. */
const SHOW_LIMITED_TIME_OFFERS = false;

export function LandingHero({
  onBook,
}: {
  /** Called with the slide's live service id when it exists, so the wizard preselects it. */
  onBook: (serviceId?: string) => void;
}) {
  const { data: config } = useQuery({
    queryKey: ["homepage-config"],
    queryFn: homepageConfigApi.get,
  });
  const { data: servicesData } = useQuery({
    queryKey: ["public-services"],
    queryFn: () => catalogApi.services({ page_size: 100 }),
  });

  const { activeIndex: activeSlide, setActiveIndex: setActiveSlide, handlers: heroHandlers } = useCarouselSwipe(HERO_SLIDES.length, 4000);
  const { trackRef: offerTrackRef, setRef: offerSetRef, handlers: offerMarqueeHandlers } = useContinuousMarquee(45);

  const slide = HERO_SLIDES[activeSlide];
  const slideServiceId = slide.serviceSlug
    ? (servicesData?.data ?? []).find((svc) => svc.slug === slide.serviceSlug)?.id
    : undefined;

  const isHome = slide.type === "home";
  const heroImagePosition: Record<string, string> = {
    home: "68% 38%",
    "star-wash": "68% 48%",
    "deep-cleaning": "64% 42%",
    waterless: "62% 42%",
    "jet-wash": "62% 45%",
    "bike-wash": "66% 45%",
  };

  return (
    <section className="bg-white">

      {/* ============================================================ */}
      {/* MAIN HERO                                                     */}
      {/* ============================================================ */}

      <WhatsAppFloatingButton />
      <div 
        className="relative isolate h-[540px] min-h-0 overflow-hidden bg-[#111] sm:h-[clamp(620px,78vh,760px)]"
        {...heroHandlers}
      >
        {/* A single, full-bleed image plane keeps the artwork and copy in one composition. */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <motion.img
            key={slide.id}
            initial={{ opacity: 0, scale: 1.045 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.68, ease: [0.22, 1, 0.36, 1] }}
            src={slide.image}
            alt={isHome ? "BLUSSIT doorstep car wash" : slide.title}
            className="absolute inset-0 h-full w-full object-cover will-change-transform"
            style={{ objectPosition: heroImagePosition[slide.id] ?? "center center" }}
          />

          {/* Cinematic left-side shading gives the copy contrast without hiding the car. */}
          <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(0,0,0,0.88)_0%,rgba(0,0,0,0.72)_18%,rgba(0,0,0,0.48)_38%,rgba(0,0,0,0.18)_60%,rgba(0,0,0,0.05)_100%)]" />
          <div className="absolute inset-0 bg-black/[0.06]" />
          <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.08)_0%,transparent_28%,transparent_62%,rgba(0,0,0,0.38)_100%)]" />
          <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(0,0,0,0.08)_0%,rgba(0,0,0,0.30)_35%,rgba(0,0,0,0.88)_100%)] sm:hidden" />
        </div>

        {/* Hero Content */}
        <div className="container-page relative z-10 flex h-full flex-col justify-center px-5 pb-16 pt-12 sm:px-8 sm:pb-56 sm:pt-14 lg:px-0 lg:pb-24">

          <motion.div
            key={slide.id}
            initial={{
              opacity: 0,
              y: 10,
            }}
            animate={{
              opacity: 1,
              y: 0,
            }}
            transition={{
              duration: 0.55,
              ease: [0.22, 1, 0.36, 1],
            }}
            className="relative z-30 max-w-[540px] pt-2"
          >

            {isHome && config?.banner_active && config?.banner_text && (
              <p className="mb-3 w-fit rounded-full bg-[#E8A900] px-3.5 py-1.5 text-[11px] font-black uppercase tracking-[0.06em] text-white shadow-[0_5px_14px_rgba(232,169,0,0.3)]">
                {config.banner_text}
              </p>
            )}

            {/* Eyebrow */}
            <div className="flex flex-wrap items-center gap-2">

              <p className="w-fit border-b border-[#E8A900]/75 pb-1 text-[10px] font-bold uppercase tracking-[0.18em] text-[#E8A900] sm:text-xs">
                {isHome
                  ? config?.hero_badge_text ||
                    slide.eyebrow
                  : slide.eyebrow}
              </p>

              {!isHome && (
                <span className="rounded-full border border-[#E8A900]/45 bg-[#E8A900]/20 px-2.5 py-1 text-[9px] font-black uppercase tracking-[0.09em] text-[#F5C542] shadow-[0_4px_12px_rgba(0,0,0,0.20)]">
                  {slide.tag}
                </span>
              )}

            </div>

            {/* Heading */}
            {isHome ? (
              <h1 className="mt-4 font-display text-[42px] font-black uppercase leading-[0.94] tracking-[-0.045em] text-white sm:text-[56px] lg:text-[68px]">
                {/* Admin Homepage Settings drives the home headline — the
                    hardcoded slide copy is only the fallback. */}
                {config?.hero_headline ? (
                  <span className="whitespace-pre-line">{config.hero_headline}</span>
                ) : (
                  <>
                    {slide.title}
                    <br />
                    {slide.titleLine2}
                    <br />
                    <span className="text-accent">
                      {slide.titleAccent}
                    </span>
                  </>
                )}
              </h1>
            ) : (
              <h1 className="mt-4 font-display text-[42px] font-black uppercase leading-[0.94] tracking-[-0.045em] text-white sm:text-[56px] lg:text-[68px]">
                {slide.title}

                {slide.titleLine2 && (
                  <>
                    <br />
                    <span className="text-accent">
                      {slide.titleLine2}
                    </span>
                  </>
                )}
              </h1>
            )}

            {/* Offer Price */}
            {!isHome && (
              <div className="mt-5 flex flex-wrap items-center gap-3">

                {slide.oldPrice && (
                  <span className="text-[15px] font-semibold text-white/55 line-through sm:text-[17px]">
                    {slide.oldPrice}
                  </span>
                )}

                <span className="text-[38px] font-black leading-none tracking-[-0.04em] text-[#E8A900] sm:text-[46px]">
                  {slide.price}
                </span>

                <span className="rounded-full bg-[#E8A900] px-3 py-1.5 text-[10px] font-black tracking-[0.04em] text-white shadow-[0_5px_14px_rgba(232,169,0,0.24)]">
                  {slide.discount}
                </span>

              </div>
            )}

            {/* Description */}
            <p className="mt-5 max-w-[500px] whitespace-pre-line text-[14px] font-medium leading-relaxed text-white/80 sm:text-[16px]">
              {isHome && config?.hero_subtext ? config.hero_subtext : slide.description}
            </p>

            {/* Items */}
            {!isHome && (
              <div className="mt-5 grid max-w-[440px] grid-cols-1 gap-x-5 gap-y-2 sm:grid-cols-2">

                {slide.items.map((item) => (
                  <div
                    key={item}
                    className="flex items-center gap-2 text-[11px] font-semibold text-white/90 sm:text-[12px]"
                  >
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#E8A900] text-[11px] text-white shadow-sm">
                      ✓
                    </span>

                    {item}
                  </div>
                ))}

              </div>
            )}

            {/* Buttons */}
            <div className="mt-8 flex flex-row gap-2.5 sm:gap-3 w-full sm:w-auto">

              <PrimaryButton
                onClick={() => onBook(slideServiceId)}
                className="!rounded-[10px] flex-1 sm:flex-none px-2 py-3 text-[11px] sm:px-6 sm:py-3.5 sm:text-[12px]"
              >
                {isHome
                  ? "Book Your Wash"
                  : "Book This Offer"}

                <ArrowRight className="h-3 w-3 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5 sm:h-3.5 sm:w-3.5" />
              </PrimaryButton>

              <OutlineButton
                onClick={() =>
                  document
                    .getElementById("services")
                    ?.scrollIntoView({
                      behavior: "smooth",
                    })
                }
                className="rounded-[10px] flex-1 sm:flex-none !border-white/55 !bg-[#141414]/35 px-2 py-3 text-[11px] sm:px-5 sm:py-3.5 sm:text-[12px] !text-white shadow-none backdrop-blur-sm hover:!border-[#E8A900] hover:!bg-[#E8A900]/15 hover:!text-white"
              >
                Explore Services
              </OutlineButton>

            </div>

          </motion.div>

          {/* Shared benefit strip: identical and static on every slide (no per-slide animation). */}
          <div
              className="absolute bottom-[52px] left-5 right-5 z-40 hidden sm:grid sm:grid-cols-4 grid-cols-2 gap-y-3 rounded-[18px] border border-white/15 bg-[#141414]/80 px-3 py-3 shadow-[0_14px_36px_rgba(0,0,0,0.28)] backdrop-blur-md sm:left-8 sm:right-8 sm:px-5 lg:bottom-[42px] lg:left-auto lg:right-[3%] lg:w-[min(46vw,760px)] lg:px-5 lg:py-4"
            >

              {HERO_FEATURES.map((f) => (
                <div
                  key={f.sub}
                  className="flex min-w-0 flex-col items-center justify-center gap-1.5 border-l border-white/10 px-2 odd:border-l-0 sm:odd:border-l sm:first:border-l-0"
                >

                  <span className="flex h-9 w-9 items-center justify-center rounded-full border border-[#E8A900]/50 bg-[#E8A900]/15 text-[#F5C542]">
                    <f.icon className="h-4 w-4" />
                  </span>

                  <div className="text-center">

                    <span className="block text-[9px] font-bold leading-tight text-white">
                      {f.label}
                    </span>

                    <span className="mt-1 block text-[8px] font-medium leading-tight text-white/60">
                      {f.sub}
                    </span>

                  </div>

                </div>
              ))}

            </div>

        </div>
        <div className="absolute bottom-7 left-5 z-50 flex items-center gap-2 sm:left-8 lg:left-1/2 lg:-translate-x-1/2">
  {HERO_SLIDES.map((heroSlide, index) => (
    <button
      key={heroSlide.id}
      type="button"
      onClick={() => setActiveSlide(index)}
      aria-label={`Go to slide ${index + 1}`}
      aria-current={activeSlide === index ? "true" : undefined}
      className={`
        h-2 rounded-full transition-all duration-300
        ${
          activeSlide === index
            ? "w-7 bg-[#E8A900]"
            : "w-2 bg-white/55 hover:bg-[#E8A900]/75"
        }
      `}
    />
  ))}
        </div>

      </div>

      {/* ============================================================ */}
      {/* FIGMA STYLE OFFERS                                            */}
      {/* ============================================================ */}

      {SHOW_LIMITED_TIME_OFFERS && (
      <section className="border-t border-black/[0.05] bg-[#FFFCF5] py-6 sm:py-7">

        <div className="container-page">

          {/* Offers Header */}
          <div className="mb-4 flex items-end justify-between px-1">

            <div>

              <div className="flex items-center gap-1.5">
                <span className="text-[14px] text-[#E8A900]">
                  ⚡
                </span>

                <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-[#D9A000] sm:text-xs">
                  Limited Time Offers
                </p>
              </div>

              <h2 className="mt-0.5 max-w-[240px] text-[20px] font-black leading-[1.05] tracking-[-0.02em] text-[#312D26] sm:max-w-none sm:text-[24px]">
                Exclusive offers
                <br />
                for you
              </h2>

            </div>

            {/* Countdown */}
            <div className="hidden items-center gap-2 md:flex">

              <span className="mr-1 text-[10px] font-medium text-[#555]">
                Offer ends in
              </span>

              {[
                ["02", "Days"],
                ["14", "Hrs"],
                ["32", "Min"],
                ["45", "Sec"],
              ].map(([number, label]) => (
                <div
                  key={label}
                  className="flex h-[62px] w-[58px] flex-col items-center justify-center rounded-[9px] border border-black/[0.06] bg-white shadow-[0_2px_8px_rgba(0,0,0,0.03)]"
                >

                  <span className="text-[20px] font-black leading-none text-[#312D26]">
                    {number}
                  </span>

                  <span className="mt-1 text-[8px] font-medium text-neutral-500">
                    {label}
                  </span>

                </div>
              ))}

            </div>

          </div>

          {/* Mobile Countdown */}
          <div className="mb-4 flex items-center justify-end gap-1.5 md:hidden">

            <span className="mr-1 text-[8px] font-medium text-neutral-500">
              Offer ends in
            </span>

            {[
              ["02", "Days"],
              ["14", "Hrs"],
              ["32", "Min"],
              ["45", "Sec"],
            ].map(([number, label]) => (
              <div
                key={label}
                className="flex h-[43px] w-[42px] flex-col items-center justify-center rounded-[7px] border border-black/[0.06] bg-white"
              >

                <span className="text-[14px] font-black leading-none text-[#312D26]">
                  {number}
                </span>

                <span className="mt-0.5 text-[6px] text-neutral-500">
                  {label}
                </span>

              </div>
            ))}

          </div>

          {/* Continuous Offer Train */}
          <div 
            className="relative overflow-hidden px-0 py-1"
            style={{ touchAction: 'pan-y' }}
            {...offerMarqueeHandlers}
          >

            <div className="pointer-events-none absolute inset-y-0 left-0 z-20 w-10 bg-gradient-to-r from-[#FFFCF5] to-transparent sm:w-14" />

            <div className="pointer-events-none absolute inset-y-0 right-0 z-20 w-10 bg-gradient-to-l from-[#FFFCF5] to-transparent sm:w-14" />

            <div
              ref={offerTrackRef}
              className="flex w-max"
              style={{
                willChange: "transform",
                transform:
                  "translate3d(0,0,0)",
              }}
            >

              {[0, 1, 2].map((set) => (
                <div
                  key={set}
                  ref={
                    set === 0
                      ? offerSetRef
                      : undefined
                  }
                  className="flex w-max shrink-0 gap-4 pr-4 sm:gap-5 sm:pr-5"
                >

                  {FIGMA_OFFERS.map(
                    (offer, index) => {
                      const isGold =
                        offer.theme === "gold";

                      return (
                        <button
                          key={`${set}-${offer.id}`}
                          type="button"
                          onClick={() => onBook()}
                          className={`group relative h-[180px] w-[290px] shrink-0 cursor-pointer overflow-hidden rounded-[12px] border text-left shadow-[0_4px_16px_rgba(0,0,0,0.06)] sm:h-[190px] sm:w-[370px] lg:w-[420px] ${
                            isGold
                              ? "border-[#E8A900]/20 bg-gradient-to-br from-[#FFD76A] via-[#F7C447] to-[#E8A900]"
                              : "border-black/[0.07] bg-white"
                          }`}
                        >

                          <img
                            src={offer.image}
                            alt=""
                            className={`absolute inset-0 h-full w-full object-cover transition-transform duration-700 group-hover:scale-[1.04] ${
                              index === 0
                                ? "object-[72%_center]"
                                : index === 1
                                ? "object-right"
                                : index === 2
                                ? "object-[75%_center]"
                                : "object-right"
                            }`}
                          />

                          <div
                            className={`absolute inset-0 ${
                              isGold
                                ? "bg-gradient-to-r from-[#FFD96D]/98 via-[#F9C84E]/88 to-[#F9C84E]/15"
                                : "bg-gradient-to-r from-white via-white/94 to-white/15"
                            }`}
                          />

                          <div className="relative z-10 flex h-full min-h-[180px] flex-col justify-between px-5 py-4 sm:min-h-[190px]">

                            <div>

                              <span className="inline-flex rounded-[5px] bg-black px-2.5 py-1 text-[8px] font-black uppercase tracking-wide text-white shadow-sm">
                                {offer.badge}
                              </span>

                              <div className="mt-4 flex items-end gap-1.5">

                                <span className="text-[42px] font-black leading-[0.9] tracking-[-0.04em] text-black">
                                  {offer.value}
                                </span>

                                <span className="pb-1 text-[14px] font-black leading-none text-black">
                                  {offer.suffix}
                                </span>

                              </div>

                              <div className="mt-2.5">

                                <p className="text-[12px] font-bold leading-[1.25] text-black sm:text-[13px]">
                                  {offer.description}
                                </p>

                                <p className="mt-1 text-[10px] font-medium leading-[1.35] text-black/75 sm:text-[11px]">
                                  {offer.subDescription}
                                </p>

                              </div>

                            </div>

                            <span className="inline-flex w-fit items-center gap-2 rounded-[6px] bg-black px-4 py-2 text-[9px] font-bold text-white shadow-md">

                              {offer.button}

                              <ArrowRight className="h-3 w-3 transition-transform duration-200 group-hover:translate-x-1" />

                            </span>

                          </div>

                        </button>
                      );
                    }
                  )}

                </div>
              ))}

            </div>

          </div>
        </div>

      </section>
      )}

      {/* ============================================================ */}
      {/* FEATURE STRIP (mobile) — the same four points as the desktop  */}
      {/* overlay, shown below the hero; static, identical on every slide */}
      {/* ============================================================ */}

      <div className="grid grid-cols-2 gap-3 border-t border-cream-line-soft bg-cream px-5 py-4 sm:hidden">

        {HERO_FEATURES.map((f) => (
          <div
            key={f.label}
            className="flex items-center gap-2.5"
          >

            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-black">
              <f.icon className="h-4 w-4" />
            </span>

            <div className="leading-tight">

              <span className="block text-[10px] font-bold text-black">
                {f.label}
              </span>

              <span className="block text-[9px] font-medium text-neutral-500">
                {f.sub}
              </span>

            </div>

          </div>
        ))}

      </div>

    </section>
  );
}

/* ------------------------------------------------------------------ */
/* WHY BLUSSIT                                                        */
/* ------------------------------------------------------------------ */

const WHY_ITEMS = [
  {
    icon: ShieldCheck,
    title: "Trusted Professionals",
    text: "Verified experts trained to deliver the best care.",
  },
  {
    icon: Droplet,
    title: "Premium Products",
    text: "We use high-quality, car-safe products for a showroom finish.",
  },
  {
    icon: Clock,
    title: "On-Time Service",
    text: "Punctual and reliable service at your doorstep.",
  },
  {
    icon: MapPin,
    title: "Doorstep Convenience",
    text: "We come to you, anytime, anywhere you need us.",
  },
  {
    icon: Sparkles,
    title: "Quality Assurance",
    text: "Every service is quality-checked for 100% satisfaction.",
  },
  {
    icon: IndianRupee,
    title: "Transparent Pricing",
    text: "No hidden charges. What you see is what you pay.",
  },
];

export function WhyBlussit() {
  return (
    <section
      id="why"
      className="bg-[#FFFCF5] px-4 py-2 sm:px-6 sm:py-3 lg:px-0 lg:py-4"
    >
      <div className="container-page">

        <div className="relative mx-auto overflow-hidden rounded-[18px] border border-[#C9B795] bg-[#FFFCF5] shadow-[0_6px_24px_rgba(24,34,44,0.045)]">

          {/* Desktop image */}
          <div className="absolute inset-0 hidden md:block">

            <div className="absolute inset-y-0 right-0 w-[67%] overflow-hidden rounded-r-[18px]">

              <img
                src={IMG.whyChoose}
                alt="BLUSSIT professional doorstep car care"
                className="h-full w-full object-cover"
                style={{
                  objectPosition: "center 48%",
                }}
              />

              <div className="absolute inset-0 bg-gradient-to-r from-[#FFFCF5] via-[#FFFCF5]/28 via-[30%] to-transparent" />

              <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-[#FFFCF5]/8" />

            </div>
          </div>

          {/* Mobile image */}
          <div className="relative h-[205px] overflow-hidden md:hidden">

            <img
              src={IMG.whyChoose}
              alt="BLUSSIT professional doorstep car care"
              className="h-full w-full object-cover"
              style={{
                objectPosition: "center 45%",
              }}
            />

            <div className="absolute inset-0 bg-gradient-to-t from-[#FFFCF5] via-transparent to-transparent" />

          </div>

          <div className="relative z-10 px-5 pb-5 pt-5 sm:px-7 sm:pb-6 sm:pt-6 md:min-h-[430px] md:px-8 md:pb-[180px] md:pt-9 lg:min-h-[470px] lg:px-10 lg:pb-[184px] lg:pt-10">

            <div className="max-w-[500px]">

              <div className="flex items-center gap-2">

                <span className="text-[14px] leading-none text-[#E8A900]">
                  ✦
                </span>

                <p className="text-[9px] font-bold uppercase tracking-[0.18em] text-[#E8A900] sm:text-[10px]">
                  Why Blussit
                </p>

              </div>

              <h2
                className="mt-3 font-['Montserrat'] text-[32px] font-extrabold leading-[0.98] tracking-[-0.04em] text-[#312D26] sm:text-[38px] md:text-[42px] lg:text-[46px]"
                style={{
                  fontFamily:
                    "Montserrat, Arial, sans-serif",
                  fontWeight: 800,
                }}
              >
                Why Choose
                <br />
                BLUSS
                <span className="text-[#E8A900]">
                  i
                </span>
                T
              </h2>

              <p className="mt-3 max-w-[450px] text-[11px] font-medium leading-[1.55] text-[#4A443B] sm:text-[12px] md:text-[13px] lg:text-[14px]">
                We go beyond just cleaning your car.
                Blussit is built to deliver trust,
                quality, and convenience every single
                time.
              </p>

            </div>

            {/* Why cards */}
            <div className="relative z-20 mt-5 grid grid-cols-2 gap-2.5 sm:mt-6 sm:grid-cols-3 sm:gap-3 md:absolute md:bottom-5 md:left-7 md:right-7 md:mt-0 md:grid-cols-6 md:gap-3 lg:left-8 lg:right-8 lg:gap-4">

              {WHY_ITEMS.map((item, index) => (
                <motion.div
                  key={item.title}
                  initial={{
                    opacity: 0,
                    y: 8,
                  }}
                  whileInView={{
                    opacity: 1,
                    y: 0,
                  }}
                  viewport={{
                    once: true,
                    margin: "-30px",
                  }}
                  transition={{
                    delay: index * 0.04,
                    duration: 0.3,
                  }}
                  className="group flex min-h-[142px] flex-col items-center rounded-[11px] border border-[#E7DFD0] bg-white px-2.5 py-3 text-center shadow-[0_5px_16px_rgba(24,34,44,0.07)] transition-all duration-200 hover:-translate-y-1 hover:border-[#E8A900]/35 hover:shadow-[0_10px_22px_rgba(24,34,44,0.11)] sm:min-h-[148px] sm:px-3 sm:py-3.5 md:min-h-[142px] lg:min-h-[158px] lg:px-3.5 lg:py-4"
                >

                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-[#E8A900] transition-transform duration-200 group-hover:scale-105 lg:h-10 lg:w-10">

                    <item.icon
                      className="h-[18px] w-[18px] lg:h-[19px] lg:w-[19px]"
                      strokeWidth={1.8}
                    />

                  </span>

                  <h3 className="mt-2.5 min-h-[25px] text-[9.5px] font-bold leading-[1.18] text-[#312D26] sm:text-[10px] lg:text-[12px]">
                    {item.title}
                  </h3>

                  <p className="mt-1.5 line-clamp-2 text-[8px] font-medium leading-[1.38] text-[#6B6255] sm:text-[9px] lg:text-[9.5px]">
                    {item.text}
                  </p>

                  <span className="mt-auto pt-2.5 text-[#E8A900] opacity-90">
                    <span className="mx-auto block h-[2px] w-7 rounded-full bg-[#E8A900]" />
                  </span>

                </motion.div>
              ))}

            </div>

          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* SERVICES                                             */
/* ------------------------------------------------------------------ */

const SERVICE_CARDS = [
  {
    icon: Car,
    title: "Exterior Wash",
    description: "High-pressure foam wash to remove dirt and grime.",
    price: "₹249",
  },
  {
    icon: Droplet,
    title: "Interior Cleaning",
    description: "Vacuuming, dusting & cleaning for a fresh and hygienic cabin.",
    price: "₹349",
  },
  {
    icon: Sparkles,
    title: "Interior Detailing",
    description: "Deep cleaning of dashboard, panels, vents & more.",
    price: "₹599",
  },
  {
    icon: Car,
    title: "Paint Protection",
    description: "Premium polish & protection for long-lasting shine.",
    price: "₹999",
  },
  {
    icon: ShieldCheck,
    title: "Ceramic Coating",
    description: "Advanced ceramic coating for ultimate protection.",
    price: "₹4,999",
  },
  {
    icon: Sparkles,
    title: "Sanitization",
    description: "Steam sanitization to kill germs and keep your car safe.",
    price: "₹399",
  },
];


function MobileServicesCarousel({ services, onBook }: { services: any[]; onBook: (s: any) => void }) {
  const [activeIndex, setActiveIndex] = useState(0);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const container = e.currentTarget;
    const scrollLeft = container.scrollLeft;
    const itemWidth = container.clientWidth;
    const newIndex = Math.round(scrollLeft / itemWidth);
    if (newIndex !== activeIndex && newIndex >= 0 && newIndex < services.length) {
      setActiveIndex(newIndex);
    }
  };

  return (
    <div className="sm:hidden block w-full mt-2 mb-4">
      <div 
        className="flex w-full overflow-x-auto snap-x snap-mandatory gap-4 pb-2 px-1 [&::-webkit-scrollbar]:hidden"
        onScroll={handleScroll}
        style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
      >
        {services.map((service, index) => {
          const Icon = service.icon || ShieldCheck; // fallback
          return (
            <motion.button
              key={service.id || `mobile-carousel-${service.title}-${index}`}
              type="button"
              disabled={!service.id}
              onClick={() => service.id && onBook(service)}
              className="group relative flex shrink-0 w-[88vw] max-w-[360px] snap-center flex-col overflow-hidden rounded-[20px] border border-[#E8E8E8] bg-white text-left shadow-[0_6px_24px_rgba(24,34,44,0.06)] disabled:cursor-default"
            >
              {/* Image Container */}
              <div className="relative w-full aspect-[1.65/1]">
                <div className="absolute inset-0 overflow-hidden bg-[#ECECEC]">
                  <img
                    src={service.image}
                    alt={service.title}
                    loading="lazy"
                    className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.045]"
                  />
                  <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/25 via-transparent to-transparent opacity-80" />
                </div>
                
                {/* Floating icon */}
                <div className="absolute bottom-[-26px] left-1/2 z-30 flex h-[52px] w-[52px] -translate-x-1/2 items-center justify-center rounded-full border-[3.5px] border-white bg-[#FFF4CD] text-[#E8A900] shadow-[0_4px_12px_rgba(24,34,44,0.12)] transition-all duration-300 group-hover:scale-105 group-hover:bg-[#E8A900] group-hover:text-white">
                  <Icon className="h-[24px] w-[24px]" strokeWidth={1.8} />
                </div>
              </div>

              {/* Card content */}
              <div className="flex flex-col px-5 pb-5 pt-[34px] text-left">
                <h3 className="text-[20px] font-bold leading-[1.2] text-[#312D26]">
                  {service.title}
                </h3>
                <p className="mt-2 min-h-[44px] text-[13px] font-medium leading-[1.45] text-[#6B6255]">
                  {service.description}
                </p>

                <div className="mt-3 mb-4 h-px w-full bg-[#F0F0F0]" />

                <div className="flex items-end justify-between">
                  <div>
                    <p className="text-[11px] font-medium text-[#746B5E]">
                      Starting at
                    </p>
                    <p className="mt-0.5 text-[24px] font-black leading-none tracking-[-0.025em] text-[#312D26]">
                      {service.price}
                    </p>
                  </div>
                  
                  <span className="inline-flex items-center justify-center gap-1.5 rounded-full bg-[#E8A900]/10 px-3 py-1.5 text-[12px] font-bold uppercase tracking-[0.05em] text-[#E8A900] transition-colors group-hover:bg-[#E8A900] group-hover:text-white">
                    {service.id ? "Book Now" : "Soon"}
                    {service.id && (
                      <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
                    )}
                  </span>
                </div>
              </div>
            </motion.button>
          );
        })}
      </div>

      {/* Dot Indicators */}
      <div className="mt-3 flex justify-center gap-2">
        {services.map((_, idx) => (
          <div
            key={idx}
            className={`h-1.5 rounded-full transition-all duration-300 ${
              activeIndex === idx
                ? "w-6 bg-[#E8A900]"
                : "w-1.5 bg-[#E8A900]/25"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

export function ServicesHorizontalScroll({
  onBook,
}: {
  onBook: (id: string) => void;
}) {
  const { data: servicesData } = useQuery({
    queryKey: ["public-services"],
    queryFn: () =>
      catalogApi.services({
        page_size: 20,
      }),
  });

  const apiServices = servicesData?.data || [];

  /* Use the same Figma cards on the dedicated /services page as well. */
  const displayServices =
    apiServices.length > 0
      ? apiServices.map((service: any, index: number) => {
          const fallback = SERVICE_CARDS[index % SERVICE_CARDS.length];
          const rawPrice =
            service?.price ??
            service?.starting_price ??
            service?.base_price ??
            service?.amount ??
            service?.startingPrice;

          let price = fallback.price;
          if (rawPrice !== undefined && rawPrice !== null && rawPrice !== "") {
            const numericPrice = Number(rawPrice);
            price = Number.isFinite(numericPrice)
              ? `₹${numericPrice.toLocaleString("en-IN")}`
              : String(rawPrice).startsWith("₹")
                ? String(rawPrice)
                : `₹${String(rawPrice)}`;
          }

          return {
            ...service,
            title: service?.name || fallback.title,
            description: service?.description || fallback.description,
            price,
            image:
              service?.image_url ||
              service?.image ||
              service?.thumbnail ||
              serviceImageFor(service?.name, index),
            icon: fallback.icon,
          };
        })
      : SERVICE_CARDS.map((service, index) => ({
          ...service,
          image: serviceImageFor(service.title, index),
        }));

  return (
    <section
      id="services"
      className="relative overflow-hidden bg-[#FFFCF5] px-4 py-2 sm:px-6 sm:py-3 md:py-4 lg:px-0 lg:py-5"
    >
      {/* Decorative dots — same visual language as the Figma */}
      <div className="pointer-events-none absolute right-5 top-5 hidden h-[115px] w-[115px] opacity-70 sm:block">
        <div
          className="h-full w-full"
          style={{
            backgroundImage:
              "radial-gradient(#E8A900 1.1px, transparent 1.1px)",
            backgroundSize: "9px 9px",
            maskImage: "linear-gradient(to bottom left, black, transparent)",
            WebkitMaskImage:
              "linear-gradient(to bottom left, black, transparent)",
          }}
        />
      </div>

      <div className="pointer-events-none absolute bottom-4 left-5 hidden h-[90px] w-[110px] opacity-60 sm:block">
        <div
          className="h-full w-full"
          style={{
            backgroundImage:
              "radial-gradient(#E8A900 1.1px, transparent 1.1px)",
            backgroundSize: "9px 9px",
            maskImage: "linear-gradient(to top right, black, transparent)",
            WebkitMaskImage:
              "linear-gradient(to top right, black, transparent)",
          }}
        />
      </div>

      <div className="container-page relative z-10">
        <div className="rounded-[18px] border border-[#C9B795] bg-[#FFFCF5] px-4 py-4 shadow-[0_5px_20px_rgba(24,34,44,0.03)] sm:px-6 sm:py-5 md:px-7 md:py-6 lg:px-8 lg:py-7">
          {/* Header */}
          <div className="mx-auto max-w-[760px] text-center">
            <div className="flex items-center justify-center gap-2">
              <span className="text-[16px] leading-none text-[#E8A900] sm:text-[18px]">
                ✦
              </span>
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#E8A900] sm:text-[11px]">
                Our Services
              </p>
            </div>

            <h2
              className="mt-2.5 font-['Montserrat'] text-[28px] font-extrabold leading-[1.05] tracking-[-0.035em] text-[#312D26] sm:text-[34px] md:text-[38px] lg:text-[40px]"
              style={{
                fontFamily: "Montserrat, Arial, sans-serif",
                fontWeight: 800,
              }}
            >
              Car Care, The{" "}
              <span>
                BLUSS<span className="text-[#E8A900]">i</span>T
              </span>{" "}
              Way
            </h2>

            <p className="mx-auto mt-2 max-w-[580px] text-[10px] font-medium leading-[1.5] text-[#5B5348] sm:text-[11px] md:text-[12px]">
              From quick washes to complete detailing, we bring professional
              car care services right to your doorstep.
            </p>
          </div>

          {/* Mobile Carousel */}
          <MobileServicesCarousel services={displayServices} onBook={(s) => s.id && onBook(s.id)} />

          {/* Desktop Grid (hidden on mobile) */}
          <div className="hidden sm:grid sm:mt-5 sm:grid-cols-3 sm:gap-3 md:gap-3.5 lg:mt-6 lg:grid-cols-6 lg:gap-4">
            {displayServices.map((service, index) => {
              const Icon = service.icon;

              return (
                <motion.button
                  key={service.id || `${service.title}-${index}`}
                  type="button"
                  disabled={!service.id}
                  onClick={() => service.id && onBook(service.id)}
                  initial={{ opacity: 0, y: 18 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{
                    delay: index * 0.055,
                    duration: 0.4,
                    ease: [0.22, 1, 0.36, 1],
                  }}
                  className="group relative flex min-w-0 cursor-pointer flex-col rounded-[11px] border border-[#E8E8E8] bg-white text-left shadow-[0_4px_14px_rgba(24,34,44,0.055)] transition-all duration-300 hover:-translate-y-1 hover:border-[#E8A900]/30 hover:shadow-[0_12px_28px_rgba(24,34,44,0.11)] disabled:cursor-default disabled:hover:translate-y-0 disabled:hover:shadow-[0_4px_14px_rgba(24,34,44,0.055)] focus:outline-none focus:ring-2 focus:ring-[#E8A900]/40"
                >
                  {/* Image */}
                  <div className="relative h-[150px] w-full sm:h-[154px] lg:h-[150px]">
                    <div className="absolute inset-0 overflow-hidden bg-[#ECECEC]">
                      <img
                        src={service.image}
                        alt={service.title}
                        loading="lazy"
                        className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.045]"
                      />
                      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/10 via-transparent to-transparent opacity-60" />
                    </div>

                    {/* Floating icon intentionally sits outside the clipped image */}
                    <div className="absolute bottom-[-24px] left-1/2 z-30 flex h-[50px] w-[50px] -translate-x-1/2 items-center justify-center rounded-full border-[5px] border-white bg-[#FFF4CD] text-[#E8A900] shadow-[0_4px_12px_rgba(24,34,44,0.10)] transition-all duration-300 group-hover:scale-105 group-hover:bg-[#E8A900] group-hover:text-white sm:h-[50px] sm:w-[50px]">
                      <Icon
                        className="h-[20px] w-[20px] sm:h-[20px] sm:w-[20px]"
                        strokeWidth={1.8}
                      />
                    </div>
                  </div>

                  {/* Card content */}
                  <div className="flex min-h-[165px] flex-1 flex-col px-3 pb-3 pt-7 text-center sm:min-h-[168px] sm:px-3.5 sm:pb-3 sm:pt-7.5 lg:min-h-[165px] lg:px-3.5">
                    <h3 className="min-h-[26px] text-[11px] font-bold leading-[1.2] text-[#312D26] sm:text-[12px] lg:text-[12.5px]">
                      {service.title}
                    </h3>

                    <p className="mt-1 min-h-[32px] text-[8.5px] font-medium leading-[1.38] text-[#6B6255] sm:text-[9px] lg:text-[9.5px]">
                      {service.description}
                    </p>

                    <div className="mx-auto mt-2 h-px w-[66%] bg-[#E8E8E8]" />

                    <div className="mt-2">
                      <p className="text-[8px] font-medium text-[#746B5E] sm:text-[9px]">
                        Starting at
                      </p>
                      <p className="mt-0.5 text-[19px] font-black leading-none tracking-[-0.025em] text-[#312D26] sm:text-[21px] lg:text-[22px]">
                        {service.price}
                      </p>
                    </div>

                    <div className="mt-auto pt-2.5">
                      <span className="mx-auto inline-flex items-center justify-center gap-1.5 text-[8px] font-bold uppercase tracking-[0.09em] text-[#E8A900] transition-all duration-200 group-hover:text-[#A87400] sm:text-[9px]">
                        {service.id ? "Book Now" : "Available Soon"}
                        {service.id && (
                          <ArrowRight className="h-3 w-3 transition-transform duration-200 group-hover:translate-x-1" />
                        )}
                      </span>
                    </div>
                  </div>
                </motion.button>
              );
            })}
          </div>

        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* SERVICES GRID — FULL CATALOG PAGE                                  */
/* ------------------------------------------------------------------ */

export function ServicesGrid({
  onBook,
}: {
  onBook: (p: any) => void;
}) {
  const { data: servicesData } = useQuery({
    queryKey: ["public-services"],
    queryFn: () =>
      catalogApi.services({
        page_size: 100,
      }),
  });

  const apiServices = servicesData?.data || [];

  /* Use the same Figma cards on the dedicated /services page as well. */
  const displayServices =
    apiServices.length > 0
      ? apiServices.map((service: any, index: number) => {
          const fallback = SERVICE_CARDS[index % SERVICE_CARDS.length];
          const rawPrice =
            service?.price ??
            service?.starting_price ??
            service?.base_price ??
            service?.amount ??
            service?.startingPrice;

          let price = fallback.price;
          if (rawPrice !== undefined && rawPrice !== null && rawPrice !== "") {
            const numericPrice = Number(rawPrice);
            price = Number.isFinite(numericPrice)
              ? `₹${numericPrice.toLocaleString("en-IN")}`
              : String(rawPrice).startsWith("₹")
                ? String(rawPrice)
                : `₹${String(rawPrice)}`;
          }

          return {
            ...service,
            title: service?.name || fallback.title,
            description: service?.description || fallback.description,
            price,
            image:
              service?.image_url ||
              service?.image ||
              service?.thumbnail ||
              serviceImageFor(service?.name, index),
            icon: fallback.icon,
          };
        })
      : SERVICE_CARDS.map((service) => ({
          ...service,
          image: serviceImageFor(service.title, SERVICE_CARDS.indexOf(service)),
        }));

  return (
    <section className="relative overflow-hidden bg-[#FFFCF5] px-4 py-5 sm:px-6 sm:py-6 lg:px-0 lg:py-7">
      <div className="pointer-events-none absolute right-5 top-5 hidden h-[115px] w-[115px] opacity-70 sm:block">
        <div
          className="h-full w-full"
          style={{
            backgroundImage:
              "radial-gradient(#E8A900 1.1px, transparent 1.1px)",
            backgroundSize: "9px 9px",
            maskImage: "linear-gradient(to bottom left, black, transparent)",
            WebkitMaskImage:
              "linear-gradient(to bottom left, black, transparent)",
          }}
        />
      </div>

      <div className="container-page relative z-10">
        <div className="rounded-[18px] border border-[#C9B795] bg-[#FFFCF5] px-4 py-5 shadow-[0_5px_20px_rgba(24,34,44,0.03)] sm:px-6 sm:py-6 md:px-7 md:py-7 lg:px-8 lg:py-8">
          <div className="mx-auto max-w-[760px] text-center">
            <div className="flex items-center justify-center gap-2">
              <span className="text-[16px] leading-none text-[#E8A900] sm:text-[18px]">
                ✦
              </span>
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#E8A900] sm:text-[11px]">
                Our Services
              </p>
            </div>

            <h1
              className="mt-3 font-['Montserrat'] text-[29px] font-extrabold leading-[1.04] tracking-[-0.035em] text-[#312D26] sm:text-[36px] md:text-[42px] lg:text-[46px]"
              style={{
                fontFamily: "Montserrat, Arial, sans-serif",
                fontWeight: 800,
              }}
            >
              Car Care, The{" "}
              <span>
                BLUSS<span className="text-[#E8A900]">i</span>T
              </span>{" "}
              Way
            </h1>

            <p className="mx-auto mt-3 max-w-[620px] text-[11px] font-medium leading-[1.55] text-[#5B5348] sm:text-[12px] md:text-[13px]">
              From quick washes to complete detailing, we bring professional
              car care services right to your doorstep.
            </p>
          </div>

          {/* Mobile Carousel */}
          <MobileServicesCarousel services={displayServices} onBook={(s) => s.id && onBook({ serviceId: s.id })} />

          {/* Desktop Grid (hidden on mobile) */}
          <div className="hidden sm:grid sm:mt-7 sm:grid-cols-3 sm:gap-3 md:gap-4 lg:mt-8 lg:grid-cols-6 lg:gap-4">
            {displayServices.map((service: any, index: number) => {
              const Icon = service.icon || SERVICE_CARDS[index % SERVICE_CARDS.length].icon;

              return (
                <motion.button
                  key={service.id || `${service.title}-${index}`}
                  type="button"
                  onClick={() =>
                    service.id && onBook({ serviceId: service.id })
                  }
                  initial={{ opacity: 0, y: 18 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{
                    delay: (index % 6) * 0.055,
                    duration: 0.4,
                    ease: [0.22, 1, 0.36, 1],
                  }}
                  className="group relative flex min-w-0 cursor-pointer flex-col overflow-hidden rounded-[11px] border border-[#E8E8E8] bg-white text-left shadow-[0_4px_14px_rgba(24,34,44,0.055)] transition-all duration-300 hover:-translate-y-1 hover:border-[#E8A900]/30 hover:shadow-[0_12px_28px_rgba(24,34,44,0.11)] focus:outline-none focus:ring-2 focus:ring-[#E8A900]/40"
                >
                  <div className="relative h-[150px] w-full sm:h-[154px] lg:h-[150px]">
                    <div className="absolute inset-0 overflow-hidden bg-[#ECECEC]">
                      <img
                        src={
                          service.image ||
                          service.image_url ||
                          service.thumbnail ||
                          serviceImageFor(service.title || service.name, index)
                        }
                        alt={service.title || service.name}
                        loading="lazy"
                        className="h-full w-full object-cover transition-transform duration-700 ease-out group-hover:scale-[1.045]"
                      />
                      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/10 via-transparent to-transparent opacity-60" />
                    </div>

                    <div className="absolute bottom-[-24px] left-1/2 z-30 flex h-[50px] w-[50px] -translate-x-1/2 items-center justify-center rounded-full border-[5px] border-white bg-[#FFF4CD] text-[#E8A900] shadow-[0_4px_12px_rgba(24,34,44,0.10)] transition-all duration-300 group-hover:scale-105 group-hover:bg-[#E8A900] group-hover:text-white sm:h-[50px] sm:w-[50px]">
                      <Icon className="h-[20px] w-[20px] sm:h-[20px] sm:w-[20px]" strokeWidth={1.8} />
                    </div>
                  </div>

                  <div className="flex min-h-[165px] flex-1 flex-col px-3 pb-3 pt-7 text-center sm:min-h-[168px] sm:px-3.5 sm:pb-3 sm:pt-7.5 lg:min-h-[165px] lg:px-3.5">
                    <h2 className="min-h-[27px] text-[11px] font-bold leading-[1.2] text-[#312D26] sm:text-[12px] lg:text-[12.5px]">
                      {service.title || service.name}
                    </h2>

                    <p className="mt-1 min-h-[34px] text-[8.5px] font-medium leading-[1.38] text-[#6B6255] sm:text-[9px] lg:text-[9.5px]">
                      {service.description || "Professional doorstep care."}
                    </p>

                    <div className="mx-auto mt-2.5 h-px w-[68%] bg-[#E8E8E8]" />

                    <div className="mt-2.5">
                      <p className="text-[8px] font-medium text-[#746B5E] sm:text-[9px]">
                        Starting at
                      </p>
                      <p className="mt-0.5 text-[19px] font-black leading-none tracking-[-0.025em] text-[#312D26] sm:text-[21px] lg:text-[22px]">
                        {service.price || SERVICE_CARDS[index % SERVICE_CARDS.length].price}
                      </p>
                    </div>

                    <div className="mt-auto pt-2.5">
                      <span className="mx-auto inline-flex items-center justify-center gap-1.5 text-[8px] font-bold uppercase tracking-[0.09em] text-[#E8A900] transition-all duration-200 group-hover:text-[#A87400] sm:text-[9px]">
                        Book Now
                        <ArrowRight className="h-3 w-3 transition-transform duration-200 group-hover:translate-x-1" />
                      </span>
                    </div>
                  </div>
                </motion.button>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
/* ------------------------------------------------------------------ */
/* VIDEO REVIEWS / CUSTOMER STORIES                                   */
/* ------------------------------------------------------------------ */

export function VideoReviewsScroll() {
  const reviews = [
    {
      quote: "Incredible service!",
      name: "Rajesh K.",
      vehicle: "BMW 5 Series",
    },
    {
      quote: "Super convenient & professional.",
      name: "Amit S.",
      vehicle: "Hyundai Creta",
    },
    {
      quote: "My car looks brand new!",
      name: "Rohit M.",
      vehicle: "Porsche",
    },
    {
      quote: "Premium service at my doorstep.",
      name: "Vikas P.",
      vehicle: "BMW",
    },
    {
      quote: "Absolutely loved the finish!",
      name: "Karan J.",
      vehicle: "Mercedes-Benz",
    },
    {
      quote: "Easy booking. Great service.",
      name: "Arjun R.",
      vehicle: "Lamborghini",
    },
  ];

  // Duplicate list for seamless infinite train
  const videoItems = [...reviews, ...reviews];

  return (
  <section className="relative overflow-hidden bg-[#FFFCF5] pt-0 pb-10 sm:pb-12">
      {/* ------------------------------------------------------------ */}
      {/* BACKGROUND DETAILS                                           */}
      {/* ------------------------------------------------------------ */}

      <div className="pointer-events-none absolute -left-32 top-10 h-64 w-64 rounded-full bg-[#FFF4CD]/50 blur-3xl" />

      <div className="pointer-events-none absolute -right-32 bottom-0 h-64 w-64 rounded-full bg-[#FFF4CD]/40 blur-3xl" />

      <div
        className="pointer-events-none absolute right-8 top-8 h-20 w-20 opacity-30"
        style={{
          backgroundImage:
            "radial-gradient(#E8A900 1px, transparent 1px)",
          backgroundSize: "9px 9px",
        }}
      />

      {/* ------------------------------------------------------------ */}
      {/* HEADER                                                        */}
      {/* ------------------------------------------------------------ */}
<div className="container-page relative mb-4 text-center">

        <div className="mb-2 flex items-center justify-center gap-2">
          <span className="text-[#E8A900]">✦</span>

          <span className="text-[10px] font-bold uppercase tracking-[0.24em] text-[#B47C00]">
            Customer Stories
          </span>
        </div>

        <h2 className="font-display text-2xl font-black tracking-tight text-[#18222C] sm:text-3xl md:text-[36px]">
          People love the{" "}
          <span className="text-[#E8A900]">BLUSSIT</span> difference.
        </h2>

        <p className="mx-auto mt-2 max-w-lg text-xs font-medium leading-5 text-[#59616A] sm:text-sm">
          Real experiences from customers who chose premium car care at
          their doorstep.
        </p>

        {/* Small rating pill */}
        <div className="mt-3 inline-flex items-center gap-2 rounded-full border border-[#E8A900]/20 bg-white px-3 py-1.5 shadow-sm">

          <div className="flex gap-0.5">
            {[1, 2, 3, 4, 5].map((star) => (
              <Star
                key={star}
                className="h-3 w-3 fill-[#E8A900] text-[#E8A900]"
              />
            ))}
          </div>

          <span className="h-3 w-px bg-[#E8A900]/20" />

          <span className="text-[9px] font-bold text-[#3E4650]">
            Loved by BLUSSIT customers
          </span>

        </div>
      </div>

      {/* ------------------------------------------------------------ */}
      {/* INFINITE VIDEO                                     */}
      {/* ------------------------------------------------------------ */}

      <div className="relative w-full overflow-hidden">

        {/* Left fade */}
        <div className="pointer-events-none absolute left-0 top-0 z-20 h-full w-16 bg-gradient-to-r from-[#FFFCF5] to-transparent" />

        {/* Right fade */}
        <div className="pointer-events-none absolute right-0 top-0 z-20 h-full w-16 bg-gradient-to-l from-[#FFFCF5] to-transparent" />

        <div className="video-review-track flex w-max gap-4">

          {videoItems.map((review, index) => (
            <div
              key={`${review.name}-${index}`}
              className="
                group
                relative
                h-[230px]
                w-[255px]
                shrink-0
                overflow-hidden
                rounded-[18px]
                border
                border-[#E4DCCF]
                bg-white
                shadow-[0_8px_25px_rgba(24,34,44,0.08)]
                transition-all
                duration-300
                hover:-translate-y-1
                hover:shadow-[0_14px_30px_rgba(24,34,44,0.13)]
                sm:h-[245px]
                sm:w-[275px]
              "
            >

              {/* Video image */}
              <img
                src={IMG.videos[index % IMG.videos.length]}
                alt="BLUSSIT customer review"
                className="
                  h-full
                  w-full
                  object-cover
                  transition-transform
                  duration-700
                  group-hover:scale-105
                "
              />

              {/* Dark cinematic gradient */}
              <div
                className="
                  absolute
                  inset-0
                  bg-gradient-to-t
                  from-[#18222C]/90
                  via-[#18222C]/20
                  to-transparent
                "
              />

              {/* ---------------------------------------------------- */}
              {/* STORY BADGE                                           */}
              {/* ---------------------------------------------------- */}

              <div className="absolute left-3 top-3">

                <span
                  className="
                    rounded-full
                    border
                    border-white/50
                    bg-white/90
                    px-2.5
                    py-1
                    text-[8px]
                    font-black
                    uppercase
                    tracking-[0.16em]
                    text-[#A87400]
                    shadow-sm
                    backdrop-blur-md
                  "
                >
                  BLUSSIT STORY
                </span>

              </div>

              {/* ---------------------------------------------------- */}
              {/* PLAY BUTTON                                           */}
              {/* ---------------------------------------------------- */}

              <div className="absolute inset-0 flex items-center justify-center">

                <span
                  className="
                    flex
                    h-11
                    w-11
                    items-center
                    justify-center
                    rounded-full
                    border
                    border-white/70
                    bg-white/90
                    text-[#18222C]
                    shadow-[0_6px_20px_rgba(0,0,0,0.18)]
                    backdrop-blur-sm
                    transition-all
                    duration-300
                    group-hover:scale-110
                    group-hover:bg-[#E8A900]
                  "
                >
                  <PlayCircle className="h-5 w-5" />
                </span>

              </div>

              {/* ---------------------------------------------------- */}
              {/* REVIEW CONTENT                                        */}
              {/* ---------------------------------------------------- */}

              <div className="absolute bottom-0 left-0 right-0 p-4">

                <p className="text-[13px] font-bold leading-tight text-white">
                  “{review.quote}”
                </p>

                {/* Stars */}
                <div className="mt-1.5 flex gap-0.5">

                  {[1, 2, 3, 4, 5].map((star) => (
                    <Star
                      key={star}
                      className="h-3 w-3 fill-[#F5B51B] text-[#F5B51B]"
                    />
                  ))}

                </div>

                {/* Customer */}
                <div className="mt-2 flex items-end justify-between">

                  <div>
                    <p className="text-[10px] font-bold text-white">
                      {review.name}
                    </p>

                    <p className="mt-0.5 text-[9px] font-medium text-white/60">
                      {review.vehicle}
                    </p>
                  </div>

                  <div className="flex h-7 w-7 items-center justify-center rounded-full border border-white/20 bg-white/10 backdrop-blur-md">
                    <span className="text-[9px] font-black text-[#F5B51B]">
                      B
                    </span>
                  </div>

                </div>
              </div>

            </div>
          ))}

        </div>
      </div>

      {/* ------------------------------------------------------------ */}
      {/* SMALL BOTTOM LINE                                            */}
      {/* ------------------------------------------------------------ */}

      <div className="relative mt-6 flex justify-center">

        <div className="flex items-center gap-2 text-[9px] font-semibold text-[#77746E]">

          <span className="h-1.5 w-1.5 rounded-full bg-[#E8A900]" />

          <span>Real customers</span>

          <span className="h-3 w-px bg-[#D8D0C2]" />

          <span>Real experiences</span>

          <span className="h-3 w-px bg-[#D8D0C2]" />

          <span>Premium car care</span>

        </div>

      </div>

      {/* ------------------------------------------------------------ */}
      {/* INFINITE TRAIN ANIMATION                                     */}
      {/* ------------------------------------------------------------ */}

      <style>{`
        .video-review-track {
          animation: blussitReviewTrain 38s linear infinite;
          will-change: transform;
        }

        .video-review-track:hover {
          animation-play-state: paused;
        }

        @keyframes blussitReviewTrain {
          from {
            transform: translateX(0);
          }

          to {
            transform: translateX(calc(-50% - 8px));
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .video-review-track {
            animation: none;
          }
        }
      `}</style>

    </section>
  );
}
/* ------------------------------------------------------------------ */
/* FINAL BOOKING CTA                                                  */
/* ------------------------------------------------------------------ */

export function FinalBookingCTA({
  onBook,
}: {
  onBook: () => void;
}) {
  return (
    <section className="bg-[#FFFCF5] px-4 py-7 sm:px-6 sm:py-9 lg:px-0">
      <div className="container-page">
        <div
          className="
            relative overflow-hidden
            rounded-[20px]
            border border-[#DCCFB5]
            bg-[#FFF8E7]
            shadow-[0_8px_24px_rgba(49,45,38,0.05)]
          "
        >
          {/* subtle accent */}
          <div className="absolute left-0 top-0 h-full w-[3px] bg-[#E8A900]" />

          <div
            className="
              relative z-10
              flex flex-col
              gap-6
              px-6 py-7
              sm:px-9 sm:py-8
              md:flex-row
              md:items-center
              md:justify-between
              md:px-11 md:py-9
            "
          >
            {/* LEFT */}
            <div className="max-w-[720px]">
              <div className="mb-2.5 flex items-center gap-2">
                <span className="h-px w-6 bg-[#E8A900]" />

                <span className="text-[9px] font-black uppercase tracking-[0.2em] text-[#B47D00] sm:text-[10px]">
                  Ready when you are
                </span>
              </div>

              <h2 className="font-display text-[28px] font-black leading-[1.05] tracking-[-0.035em] text-[#312D26] sm:text-[34px] md:text-[38px]">
                Give your car the{" "}
                <span className="text-[#E8A900]">care it deserves.</span>
              </h2>

              <p className="mt-3 max-w-[560px] text-[12px] font-medium leading-5 text-[#6B6258] sm:text-[13px]">
                Premium car care at your doorstep.
                Simple booking, professional service, and a finish you’ll love.
              </p>
            </div>

            {/* RIGHT */}
            <div className="shrink-0">
              <button
                type="button"
                onClick={() => onBook()}
                className="
                  group inline-flex
                  items-center justify-center
                  gap-3
                  rounded-[10px]
                  bg-[#E8A900]
                  px-5 py-3.5
                  text-[10px]
                  font-black
                  uppercase
                  tracking-[0.08em]
                  text-white
                  shadow-[0_7px_18px_rgba(232,169,0,0.18)]
                  transition-all duration-200
                  hover:-translate-y-0.5
                  hover:bg-[#D99A00]
                "
              >
                Book a Service

                <span className="text-[17px] leading-none transition-transform duration-200 group-hover:translate-x-1">
                  →
                </span>
              </button>

              <p className="mt-2 text-center text-[9px] font-medium text-[#8B8173]">
                Quick & easy booking
              </p>
            </div>
          </div>

          {/* bottom accent */}
          <div className="absolute bottom-0 left-8 h-[2px] w-16 rounded-full bg-[#E8A900]" />
        </div>
      </div>
    </section>
  );
}
/* ------------------------------------------------------------------ */
/* HOW IT WORKS                                                       */
/* ------------------------------------------------------------------ */

export const HOW_IT_WORKS_TRUST = [
  {
    icon: ShieldCheck,
    title: "Trusted Professionals",
    text: "Verified & experienced experts",
  },
  {
    icon: Star,
    title: "Premium Quality",
    text: "Top quality products & equipment",
  },
  {
    icon: MapPin,
    title: "At Your Doorstep",
    text: "Hassle-free service at home",
  },
];

const HOW_STEPS = [
  {
    n: "01",
    icon: Car,
    title: "Choose Your Service",
    text: "Select your car/bike and choose the service you need.",
  },
  {
    n: "02",
    icon: Calendar,
    title: "Pick Date & Time",
    text: "Select your preferred date, time and location that suits you.",
  },
  {
    n: "03",
    icon: Truck,
    title: "We Come To You",
    text: "Our professional arrives at your doorstep and takes care of the rest.",
  },
];

const HOW_IT_WORKS_BENEFITS = [
  {
    icon: ShieldCheck,
    text: "Quick & Easy Booking",
  },
  {
    icon: UserCheck,
    text: "Trusted Professionals",
  },
  {
    icon: MapPin,
    text: "At Your Doorstep",
  },
];

export function HowItWorksSection() {
  return (
    <section
      id="how-it-works"
      className="bg-white px-4 py-2.5 sm:px-6 sm:py-3 lg:px-0 lg:py-4"
    >
      <div className="container-page">
        <div className="relative overflow-hidden rounded-[18px] border border-[#C9B795]/55 bg-[#FFFCF5] shadow-[0_8px_26px_rgba(49,45,38,0.055)]">
          {/* Right-side photography */}
          <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-[66%] md:block">
            <img
              src={IMG.howItWorks}
              alt=""
              className="h-full w-full object-cover object-[68%_center]"
              style={{
                filter:
                  "grayscale(5%) saturate(92%) contrast(1.06) brightness(1.02)",
                opacity: 0.86,
              }}
            />

            {/* Cream blend keeps the copy area clean while preserving the photo */}
            <div className="absolute inset-y-0 left-0 w-[34%] bg-gradient-to-r from-[#FFFCF5] via-[#FFFCF5]/72 to-transparent" />
            <div className="absolute inset-0 bg-gradient-to-b from-[#FFF4CD]/5 via-transparent to-[#FFFCF5]/10" />
          </div>

          <div className="relative z-10 grid items-center md:grid-cols-[0.64fr_1.36fr] lg:grid-cols-[0.56fr_1.44fr]">
            {/* LEFT CONTENT */}
            <div className="px-5 py-6 sm:px-7 sm:py-7 md:px-8 md:py-8 lg:px-9 lg:py-8.5">
              <div className="flex items-center gap-2">
                <span className="text-[13px] leading-none text-[#E8A900]">
                  ✦
                </span>
                <p className="text-[8px] font-bold uppercase tracking-[0.18em] text-[#E8A900] sm:text-[9px]">
                  How It Works
                </p>
              </div>

              <h2
                className="mt-3 font-['Montserrat'] text-[30px] font-extrabold leading-[0.95] tracking-[-0.045em] text-[#312D26] sm:text-[34px] md:text-[37px] lg:text-[40px]"
                style={{
                  fontFamily: "Montserrat, Arial, sans-serif",
                  fontWeight: 800,
                }}
              >
                Car care
                <br />
                made simple
                <span className="text-[#E8A900]">.</span>
              </h2>

              <p className="mt-3 max-w-[280px] text-[10.5px] font-medium leading-[1.52] text-[#4A443B] sm:text-[11px] lg:text-[12px]">
                Book in 3 easy steps and we’ll take care of the rest.
              </p>

              {/* Benefits — intentionally different from the numbered cards */}
              <div className="mt-5 space-y-2.5 sm:mt-6">
                {HOW_IT_WORKS_BENEFITS.map((item, index) => (
                  <motion.div
                    key={item.text}
                    initial={{ opacity: 0, x: -8 }}
                    whileInView={{ opacity: 1, x: 0 }}
                    viewport={{ once: true, margin: "-30px" }}
                    transition={{ duration: 0.3, delay: index * 0.05 }}
                    className="flex items-center gap-3"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white bg-[#FFF4CD] text-[#E8A900] shadow-[0_3px_10px_rgba(49,45,38,0.07)]">
                      <item.icon
                        className="h-[15px] w-[15px]"
                        strokeWidth={1.8}
                      />
                    </span>
                    <span className="text-[9.5px] font-bold text-[#312D26] sm:text-[10px] lg:text-[11px]">
                      {item.text}
                    </span>
                  </motion.div>
                ))}
              </div>
            </div>

            {/* RIGHT PROCESS */}
            <div className="relative min-w-0 px-4 pb-6 sm:px-6 sm:pb-7 md:px-6 md:py-9 lg:px-7 lg:py-9">
              <div className="relative mx-auto max-w-[860px]">
                {/* Gold connector runs through the card centres */}
                <div className="pointer-events-none absolute left-[14%] right-[14%] top-[89px] hidden h-[2px] bg-[#E8A900]/85 sm:block">
                  <span className="absolute left-1/2 top-1/2 h-[4px] w-[4px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#E8A900]" />
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 sm:gap-6 lg:gap-7">
                  {HOW_STEPS.map((step, index) => {
                    const StepIcon = step.icon;

                    return (
                      <motion.div
                        key={step.n}
                        initial={{ opacity: 0, y: 10 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        viewport={{ once: true, margin: "-40px" }}
                        transition={{
                          duration: 0.38,
                          delay: index * 0.06,
                          ease: [0.22, 1, 0.36, 1],
                        }}
                        className="group relative z-10 flex min-h-[172px] flex-col items-center rounded-[13px] border border-white/95 bg-white/[0.93] px-3 pb-3.5 pt-5 text-center shadow-[0_8px_22px_rgba(49,45,38,0.095)] backdrop-blur-[5px] transition-all duration-300 hover:-translate-y-1 hover:bg-white/[0.97] hover:shadow-[0_13px_28px_rgba(49,45,38,0.13)] sm:min-h-[178px]"
                      >
                        {/* Floating number */}
                        <span className="absolute left-1/2 top-[-16px] z-20 flex h-[36px] w-[36px] -translate-x-1/2 items-center justify-center rounded-full border-[2px] border-[#FFFCF5] bg-[#E8A900] text-[10px] font-black text-white shadow-[0_3px_10px_rgba(49,45,38,0.14)]">
                          {step.n}
                        </span>

                        {/* Larger icon */}
                        <span className="mt-1 flex h-[60px] w-[60px] items-center justify-center rounded-full bg-[#FFF4CD] text-[#E8A900] shadow-[0_4px_13px_rgba(49,45,38,0.07)] transition-transform duration-200 group-hover:scale-105">
                          <StepIcon
                            className="h-[25px] w-[25px]"
                            strokeWidth={1.7}
                          />
                        </span>

                        <h3 className="mt-3 text-[10px] font-bold leading-[1.2] text-[#312D26] sm:text-[10.5px] md:text-[11px]">
                          {step.title}
                        </h3>

                        <p className="mx-auto mt-1.5 max-w-[150px] text-[7.8px] font-medium leading-[1.42] text-[#5B5348] sm:text-[8px]">
                          {step.text}
                        </p>

                        <span className="mt-auto pt-2.5">
                          <span className="mx-auto block h-[2px] w-7 rounded-full bg-[#E8A900]" />
                        </span>
                      </motion.div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* PREMIUM BANNER                                                     */
/* ------------------------------------------------------------------ */

export function PremiumBanner({
  onBook,
}: {
  onBook: () => void;
}) {
  return (
    <section className="bg-[#FFFCF5] py-8 sm:py-10 md:py-12">

      <div className="container-page">

        <div className="group relative min-h-[390px] overflow-hidden rounded-[28px] border border-[#C9B795]/55 bg-[#312D26] shadow-[0_18px_45px_rgba(49,45,38,0.14)] sm:min-h-[430px] md:min-h-[500px] lg:min-h-[530px]">

          {/* Premium banner image */}
          <img
            src={IMG.premium}
            alt="Premium BLUSSIT car care"
            className="absolute inset-0 h-full w-full object-cover object-center transition-transform duration-700 group-hover:scale-[1.015]"
          />

          {/* Premium dark-to-transparent blend */}
          <div className="absolute inset-0 bg-gradient-to-r from-[#312D26]/[0.98] via-[#312D26]/[0.82] via-[48%] to-[#312D26]/[0.08]" />
          <div className="absolute inset-0 bg-gradient-to-t from-[#312D26]/[0.38] via-transparent to-[#312D26]/[0.10]" />

          {/* Soft gold glow */}
          <div className="pointer-events-none absolute -left-20 -top-24 h-72 w-72 rounded-full bg-[#F5B51B]/10 blur-3xl" />

          {/* Content */}
          <div className="relative z-10 flex min-h-[390px] max-w-[620px] flex-col justify-center px-7 py-10 sm:min-h-[430px] sm:px-10 md:min-h-[500px] md:px-14 lg:min-h-[530px] lg:px-16">

            <div className="mb-5 flex items-center gap-2">
              <span className="h-px w-7 bg-[#F5B51B]" />
              <p className="text-[10px] font-bold uppercase tracking-[0.20em] text-[#F5B51B] sm:text-xs">
                Premium Car Care
              </p>
            </div>

            <h2 className="font-display max-w-[570px] text-[36px] font-black leading-[0.98] tracking-[-0.035em] text-white sm:text-[44px] md:text-[52px] lg:text-[58px]">
              More than a wash.
              <br />
              <span className="text-[#F5B51B]">It’s a finish.</span>
            </h2>

            <p className="mt-5 max-w-[500px] text-[13px] font-medium leading-[1.65] text-white/75 sm:text-[14px] md:text-[15px]">
              Upgrade your everyday clean with deeper care,
              premium products, and a finish that makes
              your ride stand out.
            </p>

            <div className="mt-6 flex flex-wrap gap-2.5">
              <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.08] px-3.5 py-2 text-[10px] font-bold text-white backdrop-blur-sm">
                <Sparkles className="h-3.5 w-3.5 text-[#F5B51B]" />
                Showroom Finish
              </span>

              <span className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/[0.08] px-3.5 py-2 text-[10px] font-bold text-white backdrop-blur-sm">
                <ShieldCheck className="h-3.5 w-3.5 text-[#F5B51B]" />
                Car-Safe Products
              </span>
            </div>

            <div className="mt-7">
              <PrimaryButton
                className="!rounded-[10px] px-6 py-3 text-[11px]"
                onClick={() => onBook()}
              >
                Explore Premium Care
                <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5" />
              </PrimaryButton>
            </div>
          </div>

          {/* Floating premium badge */}
          <div className="absolute right-5 top-5 z-20 hidden items-center gap-3 rounded-2xl border border-white/20 bg-white/[0.94] px-4 py-3 shadow-[0_10px_30px_rgba(0,0,0,0.16)] backdrop-blur-md sm:flex">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[#FFF3CF] text-[#E8A900]">
              <Star className="h-5 w-5 fill-[#E8A900]" />
            </span>
            <div>
              <p className="text-[10px] font-black uppercase tracking-wide text-[#312D26]">
                Premium Finish
              </p>
              <p className="mt-0.5 text-[9px] font-medium text-[#6B675F]">
                Care that stands out
              </p>
            </div>
          </div>

          {/* Bottom brand line */}
          <div className="absolute bottom-5 right-5 z-20 hidden items-center gap-3 rounded-xl border border-white/15 bg-[#312D26]/65 px-4 py-2.5 backdrop-blur-md lg:flex">
            <span className="text-[9px] font-black uppercase tracking-[0.16em] text-[#F5B51B]">
              BLUSSIT
            </span>
            <span className="h-3 w-px bg-white/20" />
            <span className="text-[10px] font-semibold text-white/85">
              Deep care. Better shine.
            </span>
          </div>

        </div>

      </div>

    </section>
  );
}
/* ------------------------------------------------------------------ */
/* PLANS / PRICING                                                    */
/* ------------------------------------------------------------------ */

export function PlansSplit() {
  const [billing, setBilling] = useState<"monthly" | "yearly">("monthly");

  const plans = {
    monthly: [
   {
  name: "Basic",
  price: "₹499",
  description: "Essential care for a clean, refreshed car.",
  icon: Droplet,
  popular: false,
  features: [
    "2 Exterior Wash / month",
    "1 Interior Vacuum / month",
    "Dashboard Wipe",
    "Basic Tyre Dressing",
  ],
},
{
  name: "Premium",
  price: "₹999",
  description: "Complete care for consistently clean cars.",
  icon: Sparkles,
  popular: true,
  features: [
    "4 Exterior Wash / month",
    "2 Interior Vacuum / month",
    "Dashboard Polish",
    "Tyre Dressing",
    "Car Perfume",
  ],
},
{
  name: "Elite",
  price: "₹1499",
  description: "Extra care and protection for your car.",
  icon: ShieldCheck,
  popular: false,
  features: [
    "Unlimited Exterior Wash",
    "4 Interior Vacuum / month",
    "Dashboard Polish",
    "Tyre Dressing",
    "Car Perfume",
    "Priority Booking",
  ],
},
    ],

    yearly: [
   {
  name: "Basic",
  price: "₹4,999",
  description: "Essential care with yearly savings.",
  icon: Droplet,
  popular: false,
  features: [
    "24 Exterior Wash / year",
    "12 Interior Vacuum / year",
    "Dashboard Wipe",
    "Basic Tyre Dressing",
  ],
},
{
  name: "Premium",
  price: "₹9,999",
  description: "Our most balanced yearly care plan.",
  icon: Sparkles,
  popular: true,
  features: [
    "48 Exterior Wash / year",
    "24 Interior Vacuum / year",
    "Dashboard Polish",
    "Tyre Dressing",
    "Car Perfume",
  ],
},
{
  name: "Elite",
  price: "₹14,999",
  description: "Maximum convenience and priority care.",
  icon: ShieldCheck,
  popular: false,
  features: [
    "Unlimited Exterior Wash",
    "48 Interior Vacuum / year",
    "Dashboard Polish",
    "Tyre Dressing",
    "Car Perfume",
    "Priority Booking",
  ],
},
    ],
  } as const;

  const activePlans = plans[billing];

  return (
    <section
      id="plans"
      className="relative overflow-hidden bg-[#FFFCF5] px-4 py-8 sm:px-6 sm:py-10 lg:px-0 lg:py-11"
    >
      <div className="container-page relative z-10">

        {/* Header */}
        <div className="mx-auto max-w-[700px] text-center">
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-[#E8A900]/20 bg-white px-3 py-1.5">
            <span className="h-1.5 w-1.5 rounded-full bg-[#E8A900]" />

            <span className="text-[9px] font-black uppercase tracking-[0.18em] text-[#A87400]">
              Our Plans
            </span>
          </div>

          <h2 className="font-['Montserrat'] text-[28px] font-extrabold leading-[1.05] tracking-[-0.04em] text-[#312D26] sm:text-[34px] md:text-[38px]">
            Plans that fit your{" "}
            <span className="text-[#E8A900]">car care routine.</span>
          </h2>

          <p className="mx-auto mt-2 max-w-[520px] text-[11px] font-medium leading-5 text-[#6B6255] sm:text-[12px]">
            Simple monthly or yearly plans for regular, hassle-free car care.
          </p>
        </div>

        {/* Monthly / Yearly */}
        <div className="mx-auto mt-5 flex max-w-[330px] rounded-[10px] border border-[#DCCFB8] bg-white p-1">
          {(["monthly", "yearly"] as const).map((type) => {
            const active = billing === type;

            return (
              <button
                key={type}
                type="button"
                onClick={() => setBilling(type)}
                className={`flex-1 rounded-[8px] px-4 py-2.5 text-[10px] font-bold transition-all ${
                  active
                    ? "bg-[#E8A900] text-white shadow-sm"
                    : "text-[#4F493F] hover:bg-[#FFF8E7]"
                }`}
              >
                {type === "monthly" ? "Monthly Plans" : "Yearly Plans"}
              </button>
            );
          })}
        </div>

        {/* Plans */}
        <div className="mx-auto mt-6 grid max-w-[1100px] gap-4 md:grid-cols-3">
          {activePlans.map((plan) => {
            const Icon = plan.icon;

            return (
              <div
                key={plan.name}
                className={`relative flex min-h-[380px] flex-col rounded-[17px] border bg-white px-5 py-5 shadow-[0_7px_22px_rgba(49,45,38,0.05)] ${
                  plan.popular
                    ? "border-[#E8A900] shadow-[0_8px_26px_rgba(232,169,0,0.10)]"
                    : "border-[#E4DED3]"
                }`}
              >
                {plan.popular && (
                  <span className="absolute left-1/2 top-0 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#E8A900] px-3 py-1 text-[8px] font-black uppercase tracking-[0.08em] text-white">
                    ★ Most Popular
                  </span>
                )}

                <div className="text-center">
                  <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-[#FFF4CD] text-[#B47D00]">
                    <Icon className="h-5 w-5" strokeWidth={1.8} />
                  </span>

                  <h3 className="mt-3 font-['Montserrat'] text-[19px] font-extrabold text-[#312D26]">
                    {plan.name}
                  </h3>

                  <p className="mx-auto mt-1.5 min-h-[32px] max-w-[230px] text-[9px] leading-4 text-[#6B6255]">
                    {plan.description}
                  </p>
                </div>

                <div className="mt-4 border-t border-[#E8E1D5] pt-3 text-center">
                  <span className="text-[28px] font-black tracking-[-0.04em] text-[#312D26]">
                    {plan.price}
                  </span>

                  <span className="ml-1 text-[9px] text-[#777065]">
                    / {billing === "monthly" ? "month" : "year"}
                  </span>
                </div>

                <div className="mt-4 flex-1 space-y-2">
                  {plan.features.map((feature) => (
                    <div
                      key={feature}
                      className="flex items-start gap-2"
                    >
                      <span className="mt-0.5 text-[11px] text-[#E8A900]">
                        ✓
                      </span>

                      <span className="text-[9.5px] leading-4 text-[#454038]">
                        {feature}
                      </span>
                    </div>
                  ))}
                </div>

                <PrimaryButton
                  onClick={() => {
                    document
                      .getElementById("services")
                      ?.scrollIntoView({
                        behavior: "smooth",
                        block: "start",
                      });
                  }}
                  className={`mt-5 w-full !rounded-[9px] px-4 py-2.5 text-[9px] ${
                    plan.popular
                      ? ""
                      : "!border !border-[#E8A900] !bg-white !text-[#A87400] !shadow-none hover:!bg-[#FFF8E7]"
                  }`}
                >
                  Choose Plan
                  <ArrowRight className="h-3 w-3" />
                </PrimaryButton>
              </div>
            );
          })}
        </div>

        {/* Benefits */}
        <div className="mx-auto mt-5 grid max-w-[1100px] grid-cols-2 rounded-[13px] border border-[#E6DDCE] bg-white px-2 py-2 sm:grid-cols-4">
          {[
            [ShieldCheck, "Trusted Professionals"],
            [Sparkles, "Premium Products"],
            [Truck, "At Your Doorstep"],
            [Calendar, "Easy & Flexible"],
          ].map(([Icon, title]) => (
            <div
              key={title as string}
              className="flex items-center gap-2 px-2 py-2 sm:border-r sm:border-[#E8E1D5] sm:last:border-r-0"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-[#B47D00]">
                <Icon className="h-3 w-3" strokeWidth={1.8} />
              </span>

              <span className="text-[8.5px] font-bold text-[#312D26]">
                {title as string}
              </span>
            </div>
          ))}
        </div>

      </div>
    </section>
  );
}
/* ------------------------------------------------------------------ */
/* TRUST STRIP                                                        */
/* ------------------------------------------------------------------ */

export function TrustStrip() {
  return (
    <section className="border-t border-black/5 bg-white py-20">

      <div className="container-page grid grid-cols-1 gap-12 text-center md:grid-cols-3">

        {[
          {
            icon: MapPin,
            title: "Available Everywhere",
            desc: "Serving all major locations across Indore.",
          },
          {
            icon: UserCheck,
            title: "Vetted Experts",
            desc: "Rigorous background checks and training.",
          },
          {
            icon: BadgeCheck,
            title: "The Blussit Guarantee",
            desc: "Excellence in every detail, every time.",
          },
        ].map((item, i) => (
          <div
            key={i}
            className="group flex flex-col items-center"
          >

            <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-[#E8A900] shadow-md transition-transform group-hover:scale-110">

              <item.icon className="h-8 w-8 text-black" />

            </div>

            <h3 className="text-lg font-bold text-black">
              {item.title}
            </h3>

            <p className="mt-3 max-w-[250px] text-sm font-medium text-neutral-500">
              {item.desc}
            </p>

          </div>
        ))}

      </div>

    </section>
  );
}

/* ------------------------------------------------------------------ */
/* OLD OFFERS CAROUSEL                                                */
/* ------------------------------------------------------------------ */

export const OFFERS = [

  {
    id: 1,
    title: "Star Wash",
    price: "₹350",
    old: "₹450",
    save: "Save ₹100",
    tag: "MOST POPULAR",
    items: ["Exterior Wash Foam"],
  },
  {
    id: 2,
    title: "Car + Bike Combo",
    price: "₹400",
    note: "₹350 + ₹50 add-on",
    tag: "COMBO",
    items: [
      "Interior Vacuum",
      "Dashboard Polish",
    ],
  },
  {
    id: 3,
    title: "Deep Cleaning",
    price: "₹699",
    old: "₹899",
    save: "Save ₹200",
    tag: "BEST VALUE",
    items: [
      "Exterior Wash Foam",
      "Interior Vacuum",
      "Seat Cleaning",
      "Dashboard Polish",
    ],
  },
  {
    id: 4,
    title: "Waterless Service",
    price: "₹499",
    old: "₹599",
    save: "Save ₹100",
    tag: "ECO-FRIENDLY",
    items: [
      "Exterior Wash",
      "Interior",
      "Dashboard Polish",
    ],
  },
];

export function OffersCarousel({
  onBook: _onBook,
}: {
  onBook: () => void;
}) {
  return null;
}


/* ------------------------------------------------------------------ */
/* FOOTER                                                             */
/* ------------------------------------------------------------------ */

export function Footer({
  onBook,
}: {
  onBook: () => void;
}) {
  const quickLinks = [
    { label: "Home", target: "top" },
    { label: "Services", target: "services" },
    { label: "Why BLUSSIT", target: "why" },
    { label: "How It Works", target: "how-it-works" },
  ];

  const scrollTo = (target: string) => {
    if (target === "top") {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    document
      .getElementById(target)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <footer className="relative overflow-hidden bg-[#312D26] text-[#FFFCF5]">
      {/* subtle brand glow */}
      <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-[#E8A900]/10 blur-3xl" />
      <div className="pointer-events-none absolute -left-32 bottom-0 h-64 w-64 rounded-full bg-[#FFF4CD]/[0.04] blur-3xl" />

      {/* Gold brand line */}
      <div className="h-[3px] bg-[#E8A900]" />

      <div className="container-page relative z-10 px-5 py-9 sm:px-7 sm:py-10 lg:px-0 lg:py-11">
        <div className="grid gap-9 md:grid-cols-[1.5fr_1fr_1fr] md:gap-10 lg:grid-cols-[1.6fr_1fr_1fr] lg:gap-12">
          {/* Brand */}
          <div className="max-w-[360px]">
            <button
              type="button"
              onClick={() => scrollTo("top")}
              className="group inline-flex items-center text-left"
              aria-label="Go to top"
            >
              <span className="font-['Montserrat'] text-[25px] font-black tracking-[0.18em] text-white sm:text-[28px]">
                B L U S S
                <span className="text-[#E8A900]">i</span>
                T
              </span>
            </button>

            <p className="mt-3 max-w-[320px] text-[10px] font-medium leading-[1.6] text-[#D8D0C3] sm:text-[11px]">
              Premium car care, brought right to your doorstep. Simple
              booking, trusted professionals, and quality care for every ride.
            </p>

            <button
              type="button"
              onClick={() => onBook()}
              className="mt-5 inline-flex items-center gap-2 rounded-[9px] bg-[#E8A900] px-4 py-2.5 text-[9px] font-black uppercase tracking-[0.08em] text-white shadow-[0_6px_16px_rgba(232,169,0,0.20)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#D99A00]"
            >
              Book Your Wash
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* Navigation */}
          <div>
            <p className="text-[9px] font-black uppercase tracking-[0.16em] text-[#E8A900]">
              Explore
            </p>

            <div className="mt-4 space-y-2.5">
              {quickLinks.map((link) => (
                <button
                  key={link.target}
                  type="button"
                  onClick={() => scrollTo(link.target)}
                  className="group flex items-center gap-1.5 text-[10px] font-medium text-[#D8D0C3] transition-colors duration-200 hover:text-white sm:text-[11px]"
                >
                  <span className="h-px w-0 bg-[#E8A900] transition-all duration-200 group-hover:w-3" />
                  {link.label}
                </button>
              ))}
            </div>
          </div>

          {/* Service promise */}
          <div>
            <p className="text-[9px] font-black uppercase tracking-[0.16em] text-[#E8A900]">
              BLUSSIT Promise
            </p>

            <div className="mt-4 space-y-3">
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-[#E8A900]">
                  <ShieldCheck className="h-3.5 w-3.5" strokeWidth={1.8} />
                </span>
                <div>
                  <p className="text-[9.5px] font-bold text-white">
                    Trusted Professionals
                  </p>
                  <p className="mt-0.5 text-[8px] font-medium text-[#BDB4A7]">
                    Care from trained experts.
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-[#E8A900]">
                  <MapPin className="h-3.5 w-3.5" strokeWidth={1.8} />
                </span>
                <div>
                  <p className="text-[9.5px] font-bold text-white">
                    At Your Doorstep
                  </p>
                  <p className="mt-0.5 text-[8px] font-medium text-[#BDB4A7]">
                    We come to where you are.
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#FFF4CD] text-[#E8A900]">
                  <Star className="h-3.5 w-3.5" strokeWidth={1.8} />
                </span>
                <div>
                  <p className="text-[9.5px] font-bold text-white">
                    Premium Quality
                  </p>
                  <p className="mt-0.5 text-[8px] font-medium text-[#BDB4A7]">
                    Quality-first car care.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom row */}
        <div className="mt-8 border-t border-white/10 pt-4 sm:mt-9 sm:pt-5">
          <div className="flex flex-col gap-2.5 text-[7.5px] font-medium text-[#AFA69A] sm:flex-row sm:items-center sm:justify-between sm:text-[8px]">
            <p>
              © {new Date().getFullYear()} BLUSSIT. All rights reserved.
            </p>

            <div className="flex items-center gap-4">
              <button
                type="button"
                className="transition-colors hover:text-white"
              >
                Privacy Policy
              </button>
              <button
                type="button"
                className="transition-colors hover:text-white"
              >
                Terms & Conditions
              </button>
            </div>
          </div>
        </div>
      </div>
    </footer>
  );
}

