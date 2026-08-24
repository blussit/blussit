import { Home, Clock, ShieldCheck, Repeat } from "lucide-react";
import { Reveal } from "@/components/ui/Reveal";

const items = [
  {
    icon: Home,
    title: "At Your Doorstep",
    body: "Your vehicle stays where it is.",
  },
  {
    icon: Clock,
    title: "Built Around Your Time",
    body: "Choose a convenient time instead of waiting in line.",
  },
  {
    icon: ShieldCheck,
    title: "Professional Care",
    body: "A consistent, structured service instead of an uncertain local wash.",
  },
  {
    icon: Repeat,
    title: "Made for Regular Care",
    body: "One-time cleaning today. Recurring vehicle care tomorrow.",
  },
];

export function WhyBlussit() {
  return (
    <section id="why" className="mx-auto max-w-content px-6 py-20 md:px-10">
      <Reveal className="mx-auto max-w-xl text-center">
        <div className="text-[11px] font-extrabold uppercase tracking-[0.2em] text-goldDeep">
          Why BLUSSIT
        </div>
        <h2 className="mt-2 text-[2.1rem] font-extrabold tracking-[-0.03em] text-ink sm:text-4xl">
          Car care, without the hassle.
        </h2>
      </Reveal>

      <div className="mt-12 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {items.map((item, i) => (
          <Reveal key={item.title} delay={i * 0.06}>
            <div className="h-full rounded-2xl border border-border bg-white p-3.5 sm:p-5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-goldSoft sm:h-10 sm:w-10">
                <item.icon size={17} className="text-goldDeep" strokeWidth={2} />
              </div>
              <strong className="mt-3 block text-[13px] font-bold text-ink sm:mt-4 sm:text-[15px]">
                {item.title}
              </strong>
              <p className="mt-1.5 text-[12px] leading-relaxed text-muted sm:text-[13px]">
                {item.body}
              </p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
