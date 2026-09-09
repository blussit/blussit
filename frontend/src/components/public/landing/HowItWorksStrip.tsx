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
    <SectionShell id={id} className="border-y border-cream-line-soft bg-cream-deep">
      <div className="grid gap-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,2fr)] lg:gap-14">
        <div>
          <h2 className="font-display text-[28px] font-bold leading-[1.1] tracking-tight text-black sm:text-[36px]">
            How it works
          </h2>
          <p className="mt-2 max-w-sm text-[15px] leading-relaxed text-neutral-600 sm:text-[16px]">
            No driving to a wash centre. No waiting. Book once and we handle the rest.
          </p>
        </div>

        <ol className="grid gap-6 sm:grid-cols-3 sm:gap-8">
          {STEPS.map((step, i) => (
            <li key={step.title} className="flex gap-4 sm:flex-col sm:gap-4">
              <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-black text-white">
                <step.icon className="h-5 w-5" strokeWidth={1.8} />
              </span>
              <div>
                <p className="text-[12px] font-semibold uppercase tracking-[0.12em] text-neutral-500">Step {i + 1}</p>
                <h3 className="mt-1 font-display text-[17px] font-bold text-black">{step.title}</h3>
                <p className="mt-1.5 text-[14px] leading-relaxed text-neutral-600">{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </SectionShell>
  );
}
