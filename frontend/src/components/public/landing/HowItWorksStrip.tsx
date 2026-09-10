import { Armchair, CalendarClock, Car } from "lucide-react";
import { SectionShell } from "./shared";

const STEPS = [
  {
    icon: Car,
    title: "Pick a service",
    text: "Choose what your car or bike needs and see the price upfront.",
  },
  {
    icon: CalendarClock,
    title: "Choose time & address",
    text: "Pick a slot that suits you. Home, office or parking — we come there.",
  },
  {
    icon: Armchair,
    title: "Sit back and relax",
    text: "Our captain washes your vehicle at your doorstep while you watch.",
  },
];

/** Three short steps that say the one thing that matters: we come to you. */
export function HowItWorksStrip({ id = "how-it-works" }: { id?: string }) {
  return (
    <SectionShell id={id} className="border-t border-gray-100 bg-white">
      <div className="grid gap-12 lg:grid-cols-[1fr_2.5fr] lg:gap-16">
        
        {/* LEFT COLUMN: Heading */}
        <div className="max-w-sm pt-1">
          <h2 className="font-display text-[28px] font-bold leading-tight tracking-tight text-gray-900 sm:text-[32px] lg:text-[36px]">
            How it works
          </h2>
          <p className="mt-3 text-[15px] font-medium leading-relaxed text-gray-500 sm:text-[16px]">
            No driving to a wash centre. No waiting. Book once and we handle the rest.
          </p>
        </div>

        {/* RIGHT COLUMN: 3 Steps */}
        <div className="relative">
          {/* Horizontal connecting line (Desktop) */}
          <div 
            className="absolute left-[16.66%] right-[16.66%] top-[22px] hidden h-px bg-[#E5E5E5] sm:block" 
            aria-hidden="true" 
          />
          
          {/* Vertical connecting line (Mobile) */}
          <div 
            className="absolute bottom-[60px] left-[22px] top-[22px] block w-px bg-[#E5E5E5] sm:hidden" 
            aria-hidden="true" 
          />

          <ol className="relative z-10 grid gap-10 sm:grid-cols-3 sm:gap-6 lg:gap-8">
            {STEPS.map((step, i) => (
              <li key={step.title} className="group relative flex gap-5 sm:flex-col sm:gap-6">
                
                <div className="relative flex shrink-0 sm:justify-center">
                  <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[var(--color-gold)] text-[#312D26] shadow-sm ring-[6px] ring-white transition-transform duration-300 group-hover:scale-110">
                    <step.icon className="h-5 w-5" strokeWidth={2.2} />
                  </span>
                </div>
                
                <div className="pt-1 sm:pt-0 sm:text-center">
                  <p className="text-[12px] font-bold uppercase tracking-[0.15em] text-[var(--color-gold)]">
                    Step {i + 1}
                  </p>
                  <h3 className="mt-2 font-display text-[18px] font-bold text-gray-900 sm:text-[19px]">
                    {step.title}
                  </h3>
                  <p className="mt-2 text-[14px] leading-relaxed text-gray-500">
                    {step.text}
                  </p>
                </div>

              </li>
            ))}
          </ol>
        </div>
      </div>
    </SectionShell>
  );
}
