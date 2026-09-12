import { useEffect, useState } from "react";

const LAUNCH_DATE = new Date("2026-09-14T00:00:00+05:30").getTime();

function remaining() {
  const distance = LAUNCH_DATE - Date.now();
  if (distance <= 0) return null;
  return {
    days: Math.floor(distance / 86400000),
    hours: Math.floor((distance % 86400000) / 3600000),
    minutes: Math.floor((distance % 3600000) / 60000),
    seconds: Math.floor((distance % 60000) / 1000),
  };
}

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * Pre-launch strip above the public navbar: a glossy black band with the
 * launch line on the left and a monospaced countdown on the right.
 * Deliberately plain — no emoji, no exclamation, one weight of type — so
 * it reads as an announcement rather than a promo banner. It removes
 * itself the moment the launch date passes.
 */
export function LaunchCountdownBar() {
  // Seeded synchronously so the bar never paints 00:00:00:00 for a tick.
  const [timeLeft, setTimeLeft] = useState(remaining);

  useEffect(() => {
    const timer = setInterval(() => setTimeLeft(remaining()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!timeLeft) return null;

  const units = [
    { value: timeLeft.days, label: "Days" },
    { value: timeLeft.hours, label: "Hrs" },
    { value: timeLeft.minutes, label: "Min" },
    { value: timeLeft.seconds, label: "Sec" },
  ];

  return (
    <div className="relative w-full overflow-hidden bg-[#050505] shadow-[inset_0_1px_0_rgba(255,255,255,0.18),inset_0_-1px_0_rgba(0,0,0,0.95),0_10px_26px_-12px_rgba(0,0,0,0.95)]">
      {/* 1. The body of the lacquer: a bright top face falling to near
             black, then lifting slightly at the very bottom so the bar
             reads as a rounded, lit surface rather than a flat fill. */}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,#2A2A2A_0%,#151515_38%,#070707_68%,#101010_100%)]" />

      {/* 2. Screen texture: hairline vertical ruling, barely there — it
             gives the black something for the light to catch. */}
      <div className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:repeating-linear-gradient(90deg,rgba(255,255,255,0.85)_0px,rgba(255,255,255,0.85)_1px,transparent_1px,transparent_4px)]" />

      {/* 3. Ambient glare: a wide soft highlight drifting side to side,
             like a light source reflected in a screen. */}
      <div className="pointer-events-none absolute -inset-y-6 inset-x-0 animate-sheen-drift bg-[radial-gradient(55%_150%_at_38%_-25%,rgba(255,255,255,0.16),transparent_62%)]" />

      {/* 4. Specular streaks: two identical bands half a cycle apart, so
             one is always travelling across the bar. */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-[22%] animate-glare bg-[linear-gradient(105deg,transparent_0%,rgba(255,255,255,0.10)_35%,rgba(255,255,255,0.42)_50%,rgba(255,255,255,0.10)_65%,transparent_100%)] blur-[3px]" />
      <div
        className="pointer-events-none absolute inset-y-0 left-0 w-[10%] animate-glare bg-[linear-gradient(105deg,transparent_0%,rgba(255,255,255,0.16)_40%,rgba(255,255,255,0.55)_50%,rgba(255,255,255,0.16)_60%,transparent_100%)] blur-[1px]"
        style={{ animationDelay: "-3s" }}
      />

      {/* 5. Edges: a bevel highlight on top, a dark seat at the bottom,
             plus a faint reflected glow just under the top edge. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/45 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 top-px h-5 bg-gradient-to-b from-white/[0.07] to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-black" />

      <div className="container-page relative flex flex-col items-center justify-between gap-2 py-2.5 sm:flex-row sm:gap-6">
        <p className="font-display text-[10px] font-semibold uppercase tracking-[0.22em] text-white/85 sm:text-[11px]">
          Launching 14 September 2026
          <span className="mx-2.5 text-white/20">/</span>
          <span className="font-normal tracking-[0.18em] text-white/45">Indore</span>
        </p>

        <div className="flex items-center gap-2 sm:gap-2.5">
          {units.map((u, i) => (
            <div key={u.label} className="flex items-center gap-2 sm:gap-2.5">
              {i > 0 && <span className="-mt-2 text-xs text-white/20">:</span>}
              <span className="flex flex-col items-center">
                <span className="font-mono-num text-sm font-semibold leading-none tracking-wider text-[#E8A900] sm:text-[15px]">
                  {pad(u.value)}
                </span>
                <span className="mt-1 text-[8px] font-medium uppercase tracking-[0.16em] text-white/35">{u.label}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
