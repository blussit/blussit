import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ArrowRight, MapPin, Play } from "lucide-react";
import { WhatsAppFloatingButton } from "./WhatsAppFloatingButton";

// Design palette (sampled from the approved v2 mockup).
const NAVY = "#0E1A33";
const BLUE = "#0A66F0";
const MUTED = "#5F6878";
const YELLOW = "#FFD21F";
const ICON_BG = "#EEF3FA";
const INSTAGRAM_URL = "https://www.instagram.com/blussitwash/";

// Hero photos — WebP copies of the shoot images (the PNGs are 2+ MB each).
const HERO_DESKTOP = "/img/hero-desktop-v2-1747.webp";
const HERO_DESKTOP_SMALL = "/img/hero-desktop-v2-1280.webp";
// Phones/tablets: a square shot with open sky top-left for the copy and the
// "Clean Cars Greener Indore" script top-right.
const HERO_MOBILE_SRCSET = "/img/hero-mobile-v2-720.webp 720w, /img/hero-mobile-v2-1080.webp 1080w, /img/hero-mobile-v2-1254.webp 1254w";
// The desktop and phone layouts are separate blocks, one hidden per
// breakpoint — but a hidden <img> still DOWNLOADS. Each photo therefore sits
// in a <picture> whose source only matches its own breakpoint; elsewhere the
// <img> falls back to this inline pixel, so a phone never pays for the
// desktop photo (or the other way round) and the real one isn't slowed down.
const NO_IMAGE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

// The trust row. Change the numbers here as they grow.
const CARS_wash = { to: 140, suffix: "+" };
const RATING = { to: 4.9, decimals: 1, suffix: "/5" };
const WATER_SAVED = { to: 2000, suffix: "+ L" };
// Three illustrated (not real) customer faces beside the rating.
type Avatar = { bg: string; skin: string; hair: string; shirt: string; long?: boolean; beard?: boolean };
const AVATARS: Avatar[] = [
  { bg: "#DCE8FB", skin: "#C68642", hair: "#1B1B1B", shirt: "#0A66F0" },
  { bg: "#FFF1C2", skin: "#E3B07E", hair: "#3B2416", shirt: "#E8A900", long: true },
  { bg: "#DDF3E6", skin: "#8D5524", hair: "#141414", shirt: "#0E1A33", beard: true },
];

/* ---------- solid icons, as drawn in the design ---------- */
function LeafIcon() {
  return (
    <svg width="68%" height="68%" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20.5 3C11.4 3 4.5 7.6 4.5 14.6c0 2.2.7 4.2 1.9 5.7 1.4.8 3 1.2 4.8 1.2C17.6 21.5 21.4 14.4 20.5 3Z" fill="#12A150" />
      <path d="M6.6 20.4C8.9 15.2 12.6 11.3 17 8.8" stroke="#FFFFFF" strokeWidth="1.7" strokeLinecap="round" fill="none" />
    </svg>
  );
}
function HouseIcon() {
  return (
    <svg width="68%" height="68%" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2.6 2.4 10.7a1 1 0 0 0 .64 1.77H4.8V20a1 1 0 0 0 1 1h12.4a1 1 0 0 0 1-1v-7.53h1.76a1 1 0 0 0 .64-1.77L12 2.6Z" fill={BLUE} />
      <rect x="10.1" y="14.6" width="3.8" height="6.4" rx="0.7" fill="#FFFFFF" />
    </svg>
  );
}
function ShieldIcon() {
  return (
    <svg width="68%" height="68%" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 2.2 4.2 5.1v6.3c0 4.7 3.2 8.9 7.8 10.4 4.6-1.5 7.8-5.7 7.8-10.4V5.1L12 2.2Z" fill={BLUE} />
      <path d="m8.4 12.1 2.5 2.5 4.8-5" stroke="#FFFFFF" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

const FEATURES: { Icon: () => ReactNode; line1: string; line2: string }[] = [
  { Icon: LeafIcon, line1: "Waterless", line2: "Options" },
  { Icon: HouseIcon, line1: "At Your", line2: "Doorstep" },
  { Icon: ShieldIcon, line1: "Trusted", line2: "Professionals" },
];

function CountUp({ to, decimals = 0, duration = 1800, group = true }: { to: number; decimals?: number; duration?: number; group?: boolean }) {
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
      {value.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: group })}
    </span>
  );
}

const scrollToHowItWorks = () => document.getElementById("how-it-works")?.scrollIntoView({ behavior: "smooth" });

/** `offer` (phones/tablets only) floats over the bottom of the photo, just
 *  above the features row; the desktop strip is placed by the page. */
export function LandingHero({ onBook, offer }: { onBook: (serviceSlug?: string) => void; offer?: ReactNode }) {
  return (
    <section className="relative w-full overflow-hidden bg-white">
      <WhatsAppFloatingButton />

      {/* ============================ LAPTOP / DESKTOP ============================ */}
      <div className="relative hidden min-h-[640px] overflow-hidden lg:block xl:min-h-[680px]">
        {/* Always the photo's full height, pinned right: wide screens extend
            its own white haze to the left, narrow ones trim from the left —
            the script and the whole car stay in view at every size. */}
        <picture>
          <source media="(min-width: 1440px)" srcSet={HERO_DESKTOP} />
          <source media="(min-width: 1024px)" srcSet={HERO_DESKTOP_SMALL} />
          <img
            src={NO_IMAGE}
            alt="Blussit captain washing a car at the customer's doorstep"
            fetchPriority="high"
            className="animate-hero-zoom absolute bottom-0 right-0 top-0 h-full w-auto max-w-none origin-[75%_55%]"
          />
        </picture>
        {/* Keeps the copy legible: stronger on smaller laptops, where the
            photo is cropped tighter and the car reaches under the text. */}
        <div
          className="pointer-events-none absolute inset-0 xl:hidden"
          style={{
            background:
              "linear-gradient(90deg, #FFFFFF 0%, rgba(255,255,255,0.96) 38%, rgba(255,255,255,0.82) 48%, rgba(255,255,255,0.3) 58%, rgba(255,255,255,0) 66%)",
          }}
        />
        <div
          className="pointer-events-none absolute inset-0 hidden xl:block"
          style={{
            background:
              "linear-gradient(90deg, #FFFFFF 0%, rgba(255,255,255,0.95) 30%, rgba(255,255,255,0.75) 40%, rgba(255,255,255,0.25) 50%, rgba(255,255,255,0) 58%)",
          }}
        />        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-white to-transparent" />

        <div className="container-page relative z-10 flex min-h-[640px] flex-col justify-center py-14 xl:min-h-[680px]">
          <div className="w-[50%] max-w-[580px] xl:w-[46%]">
            {/* Instagram sits beside the badge, in the photo's open sky —
                -my-1 keeps the row the badge's height, so nothing moves. */}
            <div className="flex items-center gap-3">
              <Badge />
              <InstagramLink className="-my-1 h-9 w-9" />
            </div>
            <h1
              className="mt-5 font-display text-[50px] font-extrabold leading-[1.02] tracking-[-0.035em] xl:text-[64px] 2xl:text-[70px]"
              style={{ color: NAVY }}
            >
              Your Car
              <br />
              Wash At
              <br />
              <span style={{ color: BLUE }}>Your Doorstep.</span>
            </h1>
            <p className="mt-5 max-w-[470px] text-[17px] leading-[1.55]" style={{ color: MUTED }}>
              Professional car wash at your location in Indore. Waterless options. Cleaner cars. Happier drives.
            </p>

            <Features className="mt-7" />

            <div className="mt-8 flex items-center gap-4">
              <BookButton onClick={() => onBook()} className="h-[56px] w-[210px] text-[16px]" />
              <WatchButton className="h-[56px] px-6 text-[15px]" />
            </div>

            <div className="mt-9 flex w-max items-center gap-8">
              <TrustRow />
              <LocationCard className="hidden xl:flex" />
            </div>
          </div>
        </div>
      </div>

      {/* ============================ PHONE / TABLET ============================ */}
      <div className="lg:hidden">
        {/* The square photo starts right under the navbar; the copy sits
            in its open sky (top-left). Every size here is a share of the
            photo's width, so on any phone the words land in the same clear
            spot — beside the "Clean Cars Greener Indore" script, above the car.
            Full width on tablets too, so the photo is never boxed in. */}
        <div className="relative aspect-square w-full overflow-hidden">
          <picture>
            <source media="(max-width: 1023px)" srcSet={HERO_MOBILE_SRCSET} sizes="100vw" />
            <img
              src={NO_IMAGE}
              alt="Blussit captain washing a car at the customer's doorstep"
              fetchPriority="high"
              className="animate-hero-zoom absolute inset-0 h-full w-full origin-[70%_60%] object-cover"
            />
          </picture>
          <div
            className="pointer-events-none absolute inset-0"
            style={{ background: "radial-gradient(85% 60% at 0% 0%, rgba(255,255,255,0.9) 0%, rgba(255,255,255,0.62) 48%, rgba(255,255,255,0) 85%)" }}
          />
          {/* A soft haze behind the copy only — fades out before the car. */}
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background: "linear-gradient(90deg, rgba(255,255,255,0.82) 0%, rgba(255,255,255,0.58) 40%, rgba(255,255,255,0) 60%)",
              WebkitMaskImage: "linear-gradient(180deg, #000 0%, #000 50%, transparent 68%)",
              maskImage: "linear-gradient(180deg, #000 0%, #000 50%, transparent 68%)",
            }}
          />

          <div className="absolute left-0 top-0 z-10 flex flex-col" style={{ padding: "4vw 0 0 5vw" }}>
            <span
              className="inline-flex w-fit items-center rounded-full bg-[#E8F0FE]/95 font-bold uppercase tracking-[0.05em]"
              style={{ color: "#1A5CE0", fontSize: "2.5vw", padding: "0.45em 0.9em" }}
            >
              Premium doorstep car care
            </span>
            <h1
              className="font-display font-extrabold leading-[1.05] tracking-[-0.03em]"
              style={{ color: NAVY, fontSize: "6.8vw", marginTop: "2.6vw" }}
            >
              Your Car
              <br />
              Wash At
              <br />
              <span className="whitespace-nowrap" style={{ color: BLUE }}>
                Your Doorstep.
              </span>
            </h1>
            <p className="leading-[1.4]" style={{ color: "#4B5466", fontSize: "3.05vw", marginTop: "2vw", maxWidth: "41vw" }}>
              Professional car wash at your location in Indore.
            </p>
          </div>

          {/* Instagram, top-right over the foliage — right of the photo's
              "Clean Cars Greener Indore" script (which ends ~84% across even
              at full zoom), so it never covers it or the copy. */}
          <InstagramLink
            className="absolute z-10"
            style={{ right: "3.5vw", top: "3.5vw", width: "clamp(30px, 8.5vw, 48px)", height: "clamp(30px, 8.5vw, 48px)" }}
          />

          {/* The offer floats just above the frosted sheet (which rides up 12vw). */}
          {offer && (
            <div className="absolute inset-x-0 z-20" style={{ bottom: "calc(12vw + 2px)" }}>
              {offer}
            </div>
          )}
        </div>

        {/* A frosted white sheet that rides up over the bottom of the photo, so
            the actions sit higher and stand out; grows with the screen. */}
        <div className="relative z-10 -mt-[12vw] rounded-t-[7vw] bg-white/80 px-[5vw] pb-[3vw] pt-[4.5vw] shadow-[0_-10px_30px_rgba(15,30,60,0.10)] backdrop-blur-xl">
          <Features compact />
          <BookButton onClick={() => onBook()} className="mt-[5vw] h-[clamp(52px,12vw,72px)] w-full text-[clamp(15.5px,3.7vw,22px)]" />
          <WatchButton className="mt-[3vw] h-[clamp(50px,11.5vw,68px)] w-full text-[clamp(14.5px,3.5vw,21px)]" />
          <TrustRow compact className="mt-[3.5vw]" />
        </div>
      </div>
    </section>
  );
}

function Badge({ small = false }: { small?: boolean }) {
  return (
    <span
      className={`inline-flex w-fit items-center rounded-full bg-[#E8F0FE] font-bold uppercase tracking-[0.05em] ${
        small ? "px-3 py-1.5 text-[10px]" : "px-3.5 py-1.5 text-[11.5px]"
      }`}
      style={{ color: "#1A5CE0" }}
    >
      Premium doorstep car care
    </span>
  );
}

/** Three trust points with thin dividers between them. */
function Features({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  return (
    <div className={`flex items-center ${compact ? "justify-between" : "w-max"} ${className}`}>
      {FEATURES.map(({ Icon, line1, line2 }, i) => (
        <div key={line1} className={`flex items-center ${compact ? "gap-[2vw]" : "gap-3"}`}>
          {i > 0 && <span className={`self-stretch border-l border-[#E4E9F1] ${compact ? "mr-[1.5vw]" : "mx-5"}`} aria-hidden="true" />}
          <span
            // Below 360 px the three points don't fit with their icons —
            // the words stay, the circles go ("Professionals" was clipped).
            className={`flex shrink-0 items-center justify-center rounded-full ${compact ? "h-[clamp(36px,9vw,60px)] w-[clamp(36px,9vw,60px)] max-[359px]:hidden" : "h-12 w-12"}`}
            style={{ backgroundColor: ICON_BG }}
          >
            <Icon />
          </span>
          <span className={`leading-[1.25] ${compact ? "text-[clamp(11.5px,2.95vw,18px)]" : "text-[14.5px]"}`}>
            <span className="block font-semibold" style={{ color: NAVY }}>
              {line1}
            </span>
            <span className={`block ${compact ? "" : "font-semibold"}`} style={{ color: compact ? MUTED : NAVY }}>
              {line2}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

function BookButton({ onClick, className = "" }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center justify-center gap-2 whitespace-nowrap rounded-2xl font-bold shadow-[0_10px_24px_rgba(255,200,0,0.32)] transition-transform hover:-translate-y-0.5 active:translate-y-0 ${className}`}
      style={{ backgroundColor: YELLOW, color: NAVY }}
    >
      Book A Wash
      <ArrowRight className="h-[1.1em] w-[1.1em]" strokeWidth={2.5} />
    </button>
  );
}

function WatchButton({ className = "" }: { className?: string }) {
  return (
    <button
      type="button"
      onClick={scrollToHowItWorks}
      className={`flex items-center justify-center gap-3 whitespace-nowrap rounded-2xl border border-[#E4E9F1] bg-white font-semibold shadow-[0_6px_18px_rgba(15,30,60,0.06)] transition-transform hover:-translate-y-0.5 active:translate-y-0 ${className}`}
      style={{ color: NAVY }}
    >
      <span className="flex h-7 w-7 items-center justify-center rounded-full sm:h-9 sm:w-9 lg:h-7 lg:w-7" style={{ backgroundColor: BLUE }}>
        <Play className="ml-0.5 h-3 w-3 text-white sm:h-4 sm:w-4 lg:h-3 lg:w-3" fill="currentColor" strokeWidth={0} />
      </span>
      Watch How It Works
    </button>
  );
}

/** "140+ Cars wash | 2000+ L Water Saved | 4.9/5 Rating (faces)" — the
 * layout of the design's trust row, on a hairline. */
function TrustRow({ compact = false, className = "" }: { compact?: boolean; className?: string }) {
  // Clean bold sans for the numbers (as in the design), not the display face.
  const num = `whitespace-nowrap font-body font-bold tracking-[-0.01em] leading-none ${compact ? "text-[clamp(18px,5vw,32px)]" : "text-[25px]"}`;
  const label = `mt-1.5 whitespace-nowrap ${compact ? "text-[clamp(10.5px,2.8vw,17px)]" : "text-[13px]"}`;
  const divider = <span className="w-px self-stretch bg-[#E4E9F1]" aria-hidden="true" />;
  return (
    <div className={`flex items-center border-t border-[#EDF0F5] ${compact ? "justify-between pt-[3vw]" : "gap-6 pt-4"} ${className}`}>
      <div>
        <p className={num} style={{ color: BLUE }}>
          <CountUp to={CARS_wash.to} />
          {CARS_wash.suffix}
        </p>
        <p className={label} style={{ color: MUTED }}>
          Cars Wash
        </p>
      </div>
      {divider}
      <div>
        <p className={num} style={{ color: BLUE }}>
          <CountUp to={WATER_SAVED.to} group={false} />
          {WATER_SAVED.suffix}
        </p>
        <p className={label} style={{ color: MUTED }}>
          Water Saved
        </p>
      </div>
      {divider}
      <div className={`flex items-center ${compact ? "gap-[2vw]" : "gap-3"}`}>
        <div>
          <p className={num} style={{ color: BLUE }}>
            <CountUp to={RATING.to} decimals={RATING.decimals} />
            {RATING.suffix}
          </p>
          <p className={label} style={{ color: MUTED }}>
            {compact ? "Rating" : "Customer Rating"}
          </p>
        </div>
        <Faces size={compact ? "clamp(24px, 6.4vw, 42px)" : "34px"} />
      </div>
    </div>
  );
}

function Faces({ size }: { size: string }) {
  return (
    <div className="flex" aria-hidden="true">
      {AVATARS.map((a, i) => (
        <span
          key={i}
          className="block overflow-hidden rounded-full ring-2 ring-white"
          style={{ width: size, height: size, marginLeft: i ? `calc(${size} * -0.22)` : 0 }}
        >
          <AvatarFace a={a} />
        </span>
      ))}
    </div>
  );
}

/** A simple flat illustrated face — head, hair, smile, shoulders. */
function AvatarFace({ a }: { a: Avatar }) {
  return (
    <svg viewBox="0 0 40 40" width="100%" height="100%">
      <rect width="40" height="40" fill={a.bg} />
      {a.long && <path d="M10.5 19c0-8 4.2-12.6 9.5-12.6s9.5 4.6 9.5 12.6v11h-19z" fill={a.hair} />}
      <path d="M5 41c0-8.5 6.7-13 15-13s15 4.5 15 13z" fill={a.shirt} />
      <path d="M17 24h6v5.5c-1 1-5 1-6 0z" fill={a.skin} opacity="0.85" />
      <ellipse cx="20" cy="17.5" rx="7.6" ry="8.6" fill={a.skin} />
      {a.long ? (
        <path d="M12.2 16.5c.4-5.6 3.6-8.6 7.8-8.6s7.4 3 7.8 8.6c-2.4-2.8-5-3.9-7.8-3.9s-5.4 1.1-7.8 3.9z" fill={a.hair} />
      ) : (
        <path d="M12.3 16.2c-.2-5.9 3.3-9.3 7.7-9.3s7.9 3.4 7.7 9.3c-1-2.3-3.1-3.7-7.7-3.7s-6.7 1.4-7.7 3.7z" fill={a.hair} />
      )}
      {a.beard && <path d="M13.2 19.6c.6 4.8 3.3 6.6 6.8 6.6s6.2-1.8 6.8-6.6c-1.4 2.2-3.6 2.9-6.8 2.9s-5.4-.7-6.8-2.9z" fill={a.hair} />}
      <circle cx="17.1" cy="17.6" r="0.95" fill="#1A1A1A" />
      <circle cx="22.9" cy="17.6" r="0.95" fill="#1A1A1A" />
      <path d="M17.3 21.1c1.5 1.3 3.9 1.3 5.4 0" stroke={a.beard ? "#FFFFFF" : "#7A3E1D"} strokeWidth="0.95" strokeLinecap="round" fill="none" />
    </svg>
  );
}

/** The Instagram app mark (gradient rounded square, white camera glyph),
 * linking to our page. Inline SVG; useId keeps the gradient id unique
 * because the hero renders it twice (desktop + phone). */
function InstagramLink({ className = "", style }: { className?: string; style?: CSSProperties }) {
  const gid = `ig-grad-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <a
      href={INSTAGRAM_URL}
      target="_blank"
      rel="noopener noreferrer"
      aria-label="Blussit on Instagram"
      title="Blussit on Instagram"
      className={`block shrink-0 overflow-hidden rounded-[28%] ring-2 ring-white shadow-[0_6px_16px_rgba(15,30,60,0.18)] transition-transform hover:-translate-y-0.5 active:translate-y-0 ${className}`}
      style={style}
    >
      <svg viewBox="0 0 24 24" width="100%" height="100%" aria-hidden="true" className="block">
        <defs>
          <radialGradient id={gid} cx="0.3" cy="1.07" r="1.28">
            <stop offset="0" stopColor="#FDF497" />
            <stop offset="0.05" stopColor="#FDF497" />
            <stop offset="0.45" stopColor="#FD5949" />
            <stop offset="0.6" stopColor="#D6249F" />
            <stop offset="0.9" stopColor="#285AEB" />
          </radialGradient>
        </defs>
        <rect width="24" height="24" fill={`url(#${gid})`} />
        <rect x="5.2" y="5.2" width="13.6" height="13.6" rx="4" fill="none" stroke="#FFFFFF" strokeWidth="1.8" />
        <circle cx="12" cy="12" r="3.3" fill="none" stroke="#FFFFFF" strokeWidth="1.8" />
        <circle cx="15.9" cy="8.1" r="1" fill="#FFFFFF" />
      </svg>
    </a>
  );
}

function LocationCard({ className = "" }: { className?: string }) {
  return (
    <div className={`items-center gap-3 whitespace-nowrap rounded-2xl bg-white/95 py-3 pl-3 pr-5 shadow-[0_10px_30px_rgba(15,30,60,0.12)] backdrop-blur ${className}`}>
      <span className="flex h-10 w-10 items-center justify-center rounded-full" style={{ backgroundColor: BLUE }}>
        <MapPin className="h-[18px] w-[18px] text-white" strokeWidth={2.4} />
      </span>
      <span className="leading-tight">
        <span className="block text-[14px] font-bold" style={{ color: NAVY }}>
          Indore, MP
        </span>
        <span className="block text-[12.5px]" style={{ color: MUTED }}>
          &amp; Nearby Areas
        </span>
      </span>
    </div>
  );
}
