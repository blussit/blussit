import { type ReactNode, useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowRight,
  BadgeCheck,
  Calendar,
  CalendarCheck,
  ChevronLeft,
  ChevronRight,
  Car,
  MapPin,
  PlayCircle,
  ShieldCheck,
  Star,
  Timer,
  UserCheck,
  Users,
} from "lucide-react";
import { catalogApi, homepageConfigApi } from "../../api/catalog";

const IMG = {
  hero: "/hero-img.png",
  premium: "https://images.unsplash.com/photo-1607860108855-64acf2078ed9?q=80&w=1400&auto=format&fit=crop",
  plans: "https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=1200&auto=format&fit=crop",
  services: [
    "https://images.unsplash.com/photo-1607860108855-64acf2078ed9?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1601362840469-51e4d8d58785?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1493238792000-8113da705763?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1558618666-fcd25c85cd64?q=80&w=800&auto=format&fit=crop",
  ],
  videos: [
    "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1601362840469-51e4d8d58785?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1493238792000-8113da705763?q=80&w=600&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70?q=80&w=600&auto=format&fit=crop",
  ]
};

function PrimaryButton({ children, onClick, className = "" }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center justify-center gap-2 rounded-full bg-black px-6 py-3 md:px-8 md:py-3.5 text-sm font-bold tracking-wide text-yellow-400 transition-all hover:bg-yellow-400 hover:text-black hover:scale-105 shadow-lg ${className}`}
    >
      {children}
    </button>
  );
}

function OutlineButton({ children, onClick, className = "" }: { children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center justify-center gap-2 rounded-full border border-black/10 bg-white/50 backdrop-blur-md px-6 py-3 md:px-8 md:py-3.5 text-sm font-bold tracking-wide text-black transition-all hover:bg-yellow-400 hover:border-yellow-400 hover:text-black ${className}`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Hero                                                                */
/* ------------------------------------------------------------------ */
const HERO_FEATURES = [
  { icon: Timer, label: "Doorstep", sub: "Convenience" },
  { icon: Users, label: "Trained", sub: "Professionals" },
  { icon: CalendarCheck, label: "Easy", sub: "Booking" },
  { icon: ShieldCheck, label: "Premium", sub: "Care" },
];

export function LandingHero({ onBook }: { onBook: () => void }) {
  const { data: config } = useQuery({ queryKey: ["homepage-config"], queryFn: homepageConfigApi.get });

  return (
    <section className="relative min-h-[60vh] md:min-h-[70vh] flex items-center justify-center overflow-hidden bg-white">
      {/* Absolute Background Image with Overlay */}
      <div className="absolute inset-0 z-0">
        <img src={IMG.hero} alt="BLUSSIT Hero" className="w-full h-full object-cover opacity-80 mask-radial-fade" />
        <div className="absolute inset-0 bg-gradient-to-t from-white via-white/60 to-transparent"></div>
        <div className="absolute inset-0 bg-gradient-to-r from-white via-white/70 to-transparent"></div>
      </div>

      <div className="container-page relative z-10 pt-12 pb-20 w-full flex flex-col justify-between h-full">
        <div className="max-w-2xl mt-10">
          <motion.div initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: "easeOut" }}>
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-neutral-500 mb-6">
              {config?.hero_badge_text || "The New Standard"}
            </p>
            <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl font-black leading-[1.05] text-black tracking-tight">
              {config?.hero_headline ? (
                config.hero_headline
              ) : (
                <>
                  Vehicle care,
                  <br />
                  <span className="text-neutral-400">perfected.</span>
                </>
              )}
            </h1>
            <p className="mt-6 max-w-md text-base md:text-lg leading-relaxed text-neutral-600 font-medium">
              {config?.hero_subtext || "Professional cleaning and detailing, delivered exactly where you are. Clean, minimal, premium."}
            </p>
            <div className="mt-8 flex flex-wrap gap-4">
              <PrimaryButton onClick={onBook}>Book a Service</PrimaryButton>
              <OutlineButton onClick={() => document.getElementById("services")?.scrollIntoView({ behavior: "smooth" })}>
                View Services
              </OutlineButton>
            </div>
          </motion.div>
        </div>

        <motion.div 
          initial={{ opacity: 0, y: 20 }} 
          animate={{ opacity: 1, y: 0 }} 
          transition={{ duration: 0.8, delay: 0.3 }}
          className="mt-20 md:mt-32 grid grid-cols-2 gap-4 md:grid-cols-4 bg-white/60 backdrop-blur-md rounded-3xl p-6 md:p-8 border border-black/5 shadow-sm"
        >
          {HERO_FEATURES.map((f) => (
            <div key={f.sub} className="flex flex-col md:flex-row items-center md:items-start gap-4 text-center md:text-left group">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-black text-yellow-400 transition-all group-hover:scale-110 group-hover:bg-yellow-400 group-hover:text-black shadow-lg">
                <f.icon className="h-5 w-5" />
              </span>
              <div>
                <span className="block font-bold text-black tracking-wide text-sm">{f.label}</span>
                <span className="block text-xs font-medium text-neutral-500 uppercase tracking-wider">{f.sub}</span>
              </div>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Why BLUSSIT                                                         */
/* ------------------------------------------------------------------ */
const WHY_ITEMS = [
  { n: "01", icon: Car, title: "Your Location", text: "We bring the equipment and expertise to you." },
  { n: "02", icon: UserCheck, title: "Master Detailers", text: "Highly trained professionals you can trust." },
  { n: "03", icon: Calendar, title: "On Your Schedule", text: "Book instantly for any day or time." },
  { n: "04", icon: ShieldCheck, title: "Guaranteed Quality", text: "If it's not perfect, we'll make it right." },
];

export function WhyBlussit() {
  return (
    <section id="why" className="py-20 bg-white">
      <div className="container-page">
        <div className="max-w-2xl mb-16">
          <h2 className="font-display text-3xl sm:text-4xl font-black text-black tracking-tight">The Blussit Difference</h2>
          <p className="mt-4 text-base text-neutral-500 font-medium">
            We've completely redesigned the vehicle care experience to be effortless, transparent, and exceptionally high quality.
          </p>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-x-8 gap-y-12">
          {WHY_ITEMS.map((w, i) => (
            <motion.div
              key={w.n}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ delay: i * 0.1 }}
              className="flex flex-col border-t border-black/10 pt-6"
            >
              <div className="flex items-center justify-between mb-6">
                <w.icon className="h-8 w-8 text-black group-hover:text-yellow-500 transition-colors" />
                <span className="text-xl font-black text-yellow-400/60">{w.n}</span>
              </div>
              <h3 className="text-lg font-bold text-black mb-3">{w.title}</h3>
              <p className="text-sm text-neutral-500 font-medium leading-relaxed">{w.text}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Services Floating Row                                               */
/* ------------------------------------------------------------------ */
export function ServicesHorizontalScroll({ onBook }: { onBook: (id: string) => void }) {
  const { data: servicesData } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 20 }) });
  const services = servicesData?.data || [];

  if (!services.length) return null;

  return (
    <section id="services" className="py-20 bg-neutral-50">
      <div className="container-page mb-8 flex items-end justify-between">
        <div>
          <h2 className="font-display text-3xl sm:text-4xl font-black text-black tracking-tight">Premium Services</h2>
          <p className="mt-3 text-neutral-500 font-medium">Select a service to see details.</p>
        </div>
        <a href="/services" className="hidden md:flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-black hover:opacity-70 transition-opacity">
          View Catalog <ArrowRight className="h-4 w-4" />
        </a>
      </div>

      <div className="flex gap-4 overflow-x-auto snap-x snap-mandatory px-6 md:px-[5%] pb-12 pt-4 hide-scrollbar">
        {services.map((s, i) => (
          <div 
            key={s.id}
            onClick={() => onBook(s.id)}
            className="group relative shrink-0 snap-start w-[260px] md:w-[280px] h-[360px] overflow-hidden rounded-2xl bg-white cursor-pointer shadow-sm border border-black/5 hover:shadow-xl transition-all duration-300 flex flex-col"
          >
            <div className="h-[50%] w-full overflow-hidden">
              <img src={IMG.services[i % IMG.services.length]} alt={s.name} className="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" />
            </div>
            
            <div className="flex-1 p-6 flex flex-col bg-white z-10 relative">
              <h3 className="text-lg font-bold text-black">{s.name}</h3>
              <p className="mt-2 line-clamp-2 text-xs text-neutral-500 font-medium leading-relaxed">{s.description || "Professional doorstep care."}</p>
              
              <div className="mt-auto flex items-center justify-between border-t border-black/5 pt-4">
                <span className="text-xs font-bold text-black uppercase tracking-wider group-hover:text-yellow-600 transition-colors">Book Now</span>
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-black text-yellow-400 group-hover:bg-yellow-400 group-hover:text-black group-hover:scale-110 transition-all">
                  <ArrowRight className="h-3 w-3" />
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ServicesGrid({ onBook }: { onBook: (p: any) => void }) {
  const { data: servicesData } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = (servicesData?.data || []);

  return (
    <section className="bg-white py-12">
      <div className="container-page">
        <div className="mb-12 flex flex-col gap-4">
          <h1 className="font-display text-4xl font-black text-black tracking-tight">Full Service Catalog</h1>
          <p className="text-neutral-500 font-medium">Browse our complete range of professional vehicle care services.</p>
        </div>
        
        <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-3">
          {services.map((s, i) => (
            <motion.div
              key={s.id}
              initial={{ opacity: 0, scale: 0.95 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true }}
              transition={{ delay: (i % 6) * 0.1 }}
              onClick={() => onBook({ serviceId: s.id })}
              className="group cursor-pointer flex flex-col overflow-hidden rounded-[2rem] bg-neutral-50 hover:bg-neutral-100 transition-colors"
            >
              <div className="aspect-[4/3] w-full overflow-hidden">
                <img src={IMG.services[i % IMG.services.length]} alt={s.name} className="h-full w-full object-cover transition-transform duration-700 group-hover:scale-105" />
              </div>
              
              <div className="p-8 flex flex-col flex-1">
                <h3 className="text-xl font-black text-black">{s.name}</h3>
                <p className="mt-3 text-sm leading-relaxed text-neutral-500 flex-1">{s.description || "Professional doorstep care."}</p>
                
                <div className="mt-6 flex items-center justify-between border-t border-black/5 pt-4">
                  <span className="text-sm font-bold uppercase tracking-wider text-black group-hover:text-yellow-600 transition-colors">Book Now</span>
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-black text-yellow-400 group-hover:bg-yellow-400 group-hover:text-black group-hover:scale-110 transition-all">
                    <ArrowRight className="h-4 w-4" />
                  </span>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Video Reviews Floating Row                                          */
/* ------------------------------------------------------------------ */
export function VideoReviewsScroll() {
  return (
    <section className="py-20 bg-black text-white overflow-hidden">
      <div className="container-page mb-10 text-center">
        <h2 className="font-display text-3xl sm:text-4xl font-black tracking-tight">Hear From Our Clients</h2>
        <p className="mt-3 text-neutral-400 font-medium max-w-xl mx-auto">Real experiences from people who chose the Blussit standard.</p>
      </div>

      <div className="flex gap-4 overflow-x-auto snap-x snap-mandatory px-6 md:px-[5%] pb-12 hide-scrollbar">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div 
            key={i}
            className="group shrink-0 snap-center relative w-[280px] md:w-[320px] aspect-video overflow-hidden rounded-2xl bg-neutral-900 cursor-pointer border border-white/10"
          >
            <img src={IMG.videos[i % IMG.videos.length]} alt="Video Thumbnail" className="w-full h-full object-cover opacity-60 group-hover:opacity-80 transition-all duration-500" />
            
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white/20 backdrop-blur-sm text-white group-hover:bg-yellow-400 group-hover:text-black transition-all shadow-lg">
                <PlayCircle className="h-6 w-6" />
              </span>
            </div>
            
            <div className="absolute bottom-4 left-4 right-4 flex items-end justify-between">
              <div>
                <p className="text-sm font-bold text-white">"Incredible service!"</p>
                <div className="flex gap-1 mt-1">
                  {[1,2,3,4,5].map(star => <Star key={star} className="w-3 h-3 fill-[#F0A500] text-[#F0A500]" />)}
                </div>
              </div>
              <p className="text-xs text-neutral-300 font-medium bg-black/50 px-2 py-1 rounded backdrop-blur-md">Rajesh K.</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* How it works                                                        */
/* ------------------------------------------------------------------ */
const HOW_STEPS = [
  { n: "01", title: "Select", text: "Choose your vehicle and desired service." },
  { n: "02", title: "Schedule", text: "Pick a date and time that works for you." },
  { n: "03", title: "Relax", text: "We arrive and handle the rest." },
];

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="py-20 bg-white">
      <div className="container-page">
        <div className="text-center mb-16">
          <h2 className="font-display text-3xl sm:text-4xl font-black text-black tracking-tight">Simplicity is our signature.</h2>
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-3 gap-12 relative">
          <div className="hidden md:block absolute top-[2.5rem] left-[15%] right-[15%] h-[1px] bg-black/10"></div>
          
          {HOW_STEPS.map((s, i) => (
            <motion.div 
              key={s.n} 
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.15 }}
              className="relative flex flex-col items-center text-center bg-white z-10 px-4"
            >
              <span className="flex h-20 w-20 items-center justify-center rounded-full border-4 border-white bg-black text-yellow-400 text-xl font-black shadow-xl mb-8 group-hover:bg-yellow-400 group-hover:text-black transition-colors">
                {s.n}
              </span>
              <h3 className="text-xl font-black text-black mb-3">{s.title}</h3>
              <p className="text-sm text-neutral-500 font-medium max-w-[200px]">{s.text}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Premium banner                                                      */
/* ------------------------------------------------------------------ */
export function PremiumBanner({ onBook }: { onBook: () => void }) {
  return (
    <section className="py-12 md:py-20 bg-white">
      <div className="container-page">
        <div className="rounded-[3rem] bg-neutral-100 overflow-hidden grid grid-cols-1 md:grid-cols-2 items-center">
          <div className="p-10 md:p-16 order-2 md:order-1">
            <h2 className="font-display text-3xl sm:text-4xl font-black text-black leading-tight">Elevate your standards.</h2>
            <p className="mt-4 text-base text-neutral-500 font-medium leading-relaxed">
              Experience the pinnacle of automotive care. Impeccable attention to detail, premium products, and unmatched convenience.
            </p>
            <PrimaryButton className="mt-10" onClick={onBook}>Book Your Experience</PrimaryButton>
          </div>
          <div className="h-full min-h-[300px] md:min-h-[500px] order-1 md:order-2">
            <img src={IMG.premium} alt="Premium detailing" className="w-full h-full object-cover" />
          </div>
        </div>
      </div>
    </section>
  );
}

export function PlansSplit() {
  return null; // Not needed on LandingPage anymore, it's on PlansPage
}

/* ------------------------------------------------------------------ */
/* Trust strip                                                         */
/* ------------------------------------------------------------------ */
export function TrustStrip() {
  return (
    <section className="bg-white py-20 border-t border-black/5">
      <div className="container-page grid grid-cols-1 md:grid-cols-3 gap-12 text-center">
        {[
          { icon: MapPin, title: "Available Everywhere", desc: "Serving all major locations across Indore." },
          { icon: UserCheck, title: "Vetted Experts", desc: "Rigorous background checks and training." },
          { icon: BadgeCheck, title: "The Blussit Guarantee", desc: "Excellence in every detail, every time." }
        ].map((item, i) => (
          <div key={i} className="flex flex-col items-center group">
            <div className="flex items-center justify-center h-16 w-16 rounded-full bg-yellow-400 mb-6 shadow-md group-hover:scale-110 transition-transform">
              <item.icon className="h-8 w-8 text-black" />
            </div>
            <h3 className="font-bold text-lg text-black">{item.title}</h3>
            <p className="mt-3 text-sm text-neutral-500 font-medium max-w-[250px]">{item.desc}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Offers Carousel (Flipkart Style)                                    */
/* ------------------------------------------------------------------ */

const OFFERS = [
  {
    id: 1,
    title: "Summer Special",
    subtitle: "Flat 20% OFF on Complete Detailing",
    bgImage: "https://images.unsplash.com/photo-1601362840469-51e4d8d58785?q=80&w=1400&auto=format&fit=crop",
    gradient: "from-black/90 via-black/60 to-transparent",
    tag: "LIMITED TIME",
  },
  {
    id: 2,
    title: "First Wash Free",
    subtitle: "When you subscribe to any monthly plan",
    bgImage: "https://images.unsplash.com/photo-1552519507-da3b142c6e3d?q=80&w=1400&auto=format&fit=crop",
    gradient: "from-black/80 via-black/40 to-transparent",
    tag: "NEW USERS",
  },
  {
    id: 3,
    title: "Premium Ceramic",
    subtitle: "Now available at your doorstep. Ultimate shine.",
    bgImage: "https://images.unsplash.com/photo-1607860108855-64acf2078ed9?q=80&w=1400&auto=format&fit=crop",
    gradient: "from-black/90 via-black/50 to-transparent",
    tag: "PREMIUM",
  }
];

export function OffersCarousel({ onBook }: { onBook: () => void }) {
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrent((prev) => (prev + 1) % OFFERS.length);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const next = () => setCurrent((prev) => (prev + 1) % OFFERS.length);
  const prev = () => setCurrent((prev) => (prev - 1 + OFFERS.length) % OFFERS.length);

  return (
    <section className="pt-24 pb-4 bg-white">
      <div className="container-page">
        <div className="relative w-full h-[120px] sm:h-[160px] md:h-[220px] rounded-xl md:rounded-2xl overflow-hidden group shadow-lg">
          {OFFERS.map((offer, index) => (
            <div
              key={offer.id}
              className={`absolute inset-0 transition-opacity duration-1000 ${
                index === current ? "opacity-100 z-10" : "opacity-0 z-0"
              }`}
            >
              <img src={offer.bgImage} alt={offer.title} className="w-full h-full object-cover" />
              <div className={`absolute inset-0 bg-gradient-to-r ${offer.gradient}`}></div>
              
              <div className="absolute inset-0 flex flex-col justify-center p-5 sm:p-8 md:px-12 md:py-8 text-white max-w-xl">
                <span className="inline-block px-2 py-0.5 sm:px-3 sm:py-1 bg-yellow-400 text-black text-[9px] sm:text-[10px] font-black uppercase tracking-widest rounded-full w-fit mb-2 md:mb-3">
                  {offer.tag}
                </span>
                <h2 className="font-display text-lg sm:text-2xl md:text-3xl font-black leading-tight mb-1 md:mb-2 text-white">
                  {offer.title}
                </h2>
                <p className="text-[11px] sm:text-sm md:text-base text-gray-200 font-medium mb-3 md:mb-5 line-clamp-2">
                  {offer.subtitle}
                </p>
                <button 
                  onClick={onBook}
                  className="w-fit bg-yellow-400 text-black px-4 py-1.5 sm:px-6 sm:py-2 rounded-full text-[10px] sm:text-xs font-bold tracking-wide hover:bg-white hover:text-black hover:scale-105 transition-all shadow-md"
                >
                  Grab Offer
                </button>
              </div>
            </div>
          ))}

          {/* Controls */}
          <button 
            onClick={prev}
            className="absolute left-2 md:left-4 top-1/2 -translate-y-1/2 z-20 flex h-6 w-6 md:h-8 md:w-8 items-center justify-center rounded-full bg-black/30 backdrop-blur-md text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/50"
          >
            <ChevronLeft className="h-3 w-3 md:h-5 md:w-5" />
          </button>
          <button 
            onClick={next}
            className="absolute right-2 md:right-4 top-1/2 -translate-y-1/2 z-20 flex h-6 w-6 md:h-8 md:w-8 items-center justify-center rounded-full bg-black/30 backdrop-blur-md text-white opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/50"
          >
            <ChevronRight className="h-3 w-3 md:h-5 md:w-5" />
          </button>

          {/* Indicators */}
          <div className="absolute bottom-1 md:bottom-2 left-1/2 -translate-x-1/2 z-20 flex gap-1.5 md:gap-2">
            {OFFERS.map((_, idx) => (
              <button
                key={idx}
                onClick={() => setCurrent(idx)}
                className={`h-1 md:h-1.5 rounded-full transition-all ${
                  idx === current ? "w-4 md:w-6 bg-yellow-400" : "w-1 md:w-1.5 bg-white/50 hover:bg-white"
                }`}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
