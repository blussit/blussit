import { ArrowRight } from "lucide-react";
import { siteConfig } from "@/config/site";
import { Button } from "@/components/ui/Button";
import { Reveal } from "@/components/ui/Reveal";
import { useCountdown } from "@/hooks/useCountdown";
import { track } from "@/services/analytics";

function pad(n: number) {
  return n.toString().padStart(2, "0");
}

function TimeBlock({ value, label }: { value: number; label: string }) {
  return (
    <div className="rounded-2xl bg-dark px-2 py-3 text-center">
      <b className="block font-display text-2xl font-extrabold text-gold sm:text-3xl">
        {pad(value)}
      </b>
      <small className="mt-1.5 block text-[9px] font-bold uppercase tracking-widest text-white/50">
        {label}
      </small>
    </div>
  );
}

export function Hero() {
  const { days, hours, minutes, seconds, isLaunched } = useCountdown(siteConfig.launchDateISO);

  const scrollToForm = () => {
    track("hero_cta_click");
    document.getElementById("early-access")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <section
      id="top"
      className="relative overflow-hidden pb-16 pt-32 md:pb-20 md:pt-40"
      style={{
        backgroundImage:
          "radial-gradient(circle at 85% 18%, #fff1bd, transparent 45%), linear-gradient(#fff, #fffaf0)",
      }}
    >
      <div className="mx-auto grid max-w-content items-center gap-14 px-6 md:grid-cols-2 md:gap-10 md:px-10">
        <Reveal>
          <span className="inline-flex items-center gap-2 text-xs font-extrabold uppercase tracking-[0.19em] text-goldDeep">
            <span className="h-0.5 w-7 bg-gold" />
            {siteConfig.heroKicker}
          </span>

          <h1 className="mt-4 text-[3.2rem] font-extrabold leading-[0.86] tracking-[-0.04em] text-ink sm:text-7xl lg:text-[5.5rem]">
            {siteConfig.heroTitleLines.map((line) => (
              <span key={line} className="block">
                {line}
              </span>
            ))}
            <span className="block text-gold">{siteConfig.heroTitleGoldWord}</span>
          </h1>

          <p className="mt-6 max-w-md text-[17px] leading-relaxed text-muted">
            {siteConfig.heroDescription}
          </p>

          {isLaunched ? (
            <div className="mt-8 max-w-sm rounded-2xl bg-gold py-4 text-center text-xl font-extrabold text-ink">
              WE ARE LIVE 🚗
            </div>
          ) : (
            <div className="mt-8 grid max-w-md grid-cols-4 gap-2">
              <TimeBlock value={days} label="Days" />
              <TimeBlock value={hours} label="Hours" />
              <TimeBlock value={minutes} label="Minutes" />
              <TimeBlock value={seconds} label="Seconds" />
            </div>
          )}

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <div className="rounded-xl border border-border bg-white px-4 py-3 text-xs font-extrabold tracking-wide text-ink">
              LAUNCHING{" "}
              <strong className="text-goldDeep">
                {siteConfig.launchDateLabel.toUpperCase()} · {siteConfig.launchCity.toUpperCase()}
              </strong>
            </div>
            <Button onClick={scrollToForm}>
              {siteConfig.ctaPrimaryPreLaunch}
              <ArrowRight size={18} />
            </Button>
          </div>
        </Reveal>

        <Reveal
          delay={0.1}
          className="relative hidden min-h-[420px] flex-col items-center justify-center md:flex"
        >
          <img
            src="/hero-headline.webp"
            alt="No time to wash? Leave it to us!"
            className="w-full max-w-[400px]"
            width={672}
            height={286}
          />
          <img
            src="/hero-car.webp"
            alt="A dirty car transformed into a clean, shining car after a BLUSSIT wash"
            className="relative z-[3] -mt-2 w-full max-w-[560px] animate-float drop-shadow-[0_30px_60px_rgba(16,17,20,0.2)]"
            width={1085}
            height={687}
            loading="eager"
          />
        </Reveal>
      </div>

      {/* Mobile/tablet only — same headline + car visual as the desktop
          column above, shown full-width below the hero copy instead of
          side-by-side (there's no room for both columns on a phone). */}
      <Reveal delay={0.15} className="mt-12 flex flex-col items-center px-6 md:hidden">
        <img
          src="/hero-headline.webp"
          alt="No time to wash? Leave it to us!"
          className="w-full max-w-[320px]"
          width={672}
          height={286}
        />
        <img
          src="/hero-car.webp"
          alt="A dirty car transformed into a clean, shining car after a BLUSSIT wash"
          className="relative z-[3] -mt-1 w-full max-w-[420px] animate-float drop-shadow-[0_20px_40px_rgba(16,17,20,0.2)]"
          width={1085}
          height={687}
          loading="eager"
        />
      </Reveal>
    </section>
  );
}
