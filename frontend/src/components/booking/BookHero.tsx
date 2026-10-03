import { Clock, Droplets, ShieldCheck } from "lucide-react";

const TRUST = [
  { Icon: Droplets, top: "Waterless", bottom: "Options" },
  { Icon: Clock, top: "On-Time", bottom: "Service" },
  { Icon: ShieldCheck, top: "Trusted", bottom: "Professionals" },
];

/**
 * "Book Your / Car Wash" header from the approved booking mockup.
 * `page` (the public /book page): the wide photo of a captain at work, copy
 * on its bright left side. `app` (inside the customer portal shell): the
 * same words, no photo — the shell already frames the page.
 */
export function BookHero({ variant }: { variant: "page" | "app" }) {
  if (variant === "app") {
    return (
      <header className="mb-5">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#0A66F0]">Premium doorstep car care</p>
        <h1 className="mt-1.5 font-display text-[28px] font-extrabold leading-[1.08] tracking-[-0.03em] text-[#0E1A33] sm:text-[34px]">
          Book Your <span className="text-[#0A66F0]">Car Wash</span>
        </h1>
        <p className="mt-1.5 text-[14px] text-[#5F6878] sm:text-[15px]">Quick. Easy. At your doorstep in Indore.</p>
      </header>
    );
  }
  return (
    <section className="relative overflow-hidden bg-white">
      <picture>
        <source media="(min-width: 1024px)" srcSet="/img/book-hero-1600.webp" />
        <img
          src="/img/book-hero-960.webp"
          alt="Blussit captain washing a car at the customer's doorstep"
          fetchPriority="high"
          className="absolute inset-0 h-full w-full object-cover object-[72%_center] sm:object-[85%_center] lg:object-right"
        />
      </picture>
      {/* Keeps the copy legible on the photo's bright left side. */}
      <div
        className="pointer-events-none absolute inset-0 sm:hidden"
        style={{ background: "linear-gradient(90deg, #FFFFFF 0%, rgba(255,255,255,0.94) 34%, rgba(255,255,255,0.55) 52%, rgba(255,255,255,0) 68%)" }}
      />
      <div
        className="pointer-events-none absolute inset-0 hidden sm:block"
        style={{ background: "linear-gradient(90deg, #FFFFFF 0%, rgba(255,255,255,0.95) 30%, rgba(255,255,255,0.6) 44%, rgba(255,255,255,0) 58%)" }}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-[#F6F8FC] to-transparent" />

      <div className="relative mx-auto w-full max-w-[1200px] px-4 pb-12 pt-6 sm:px-6 sm:pb-20 sm:pt-12 lg:pb-24 lg:pt-14">
        <p className="hidden text-[11px] font-bold uppercase tracking-[0.2em] text-[#0A66F0] sm:block">Premium doorstep car care</p>
        <h1 className="font-display text-[32px] font-extrabold leading-[1.02] tracking-[-0.035em] text-[#0E1A33] sm:mt-3 sm:text-[46px] lg:text-[58px]">
          Book Your
          <br />
          <span className="text-[#0A66F0]">Car Wash</span>
        </h1>
        <p className="mt-2 max-w-[190px] text-[13px] leading-snug text-[#3D4757] sm:mt-3 sm:max-w-none sm:text-[18px]">Quick. Easy. At your doorstep in Indore.</p>
        <ul className="mt-6 hidden gap-6 sm:flex">
          {TRUST.map(({ Icon, top, bottom }) => (
            <li key={top} className="flex items-center gap-2.5">
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[#EEF3FA] text-[#0A66F0]">
                <Icon className="h-[18px] w-[18px]" />
              </span>
              <span className="text-[13px] leading-tight text-[#0E1A33]">
                <span className="block font-semibold">{top}</span>
                <span className="block text-[#5F6878]">{bottom}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
