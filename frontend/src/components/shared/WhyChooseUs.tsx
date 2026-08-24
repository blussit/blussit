import { BadgeCheck, Home, Leaf, ShieldCheck, Sparkle, Wallet } from "lucide-react";
import { Section, SectionHeading } from "./Section";

const points = [
  { icon: BadgeCheck, title: "Verified professionals", description: "Every captain is background-checked and trained to our quality standards." },
  { icon: Home, title: "Doorstep convenience", description: "No need to drive anywhere — we come to your home or office." },
  { icon: Wallet, title: "Transparent pricing", description: "See the exact price before you book. No hidden charges, ever." },
  { icon: Leaf, title: "Eco-friendly practices", description: "Low-water techniques and biodegradable products, where possible." },
  { icon: Sparkle, title: "Consistent quality", description: "A standardized checklist ensures the same great result, every time." },
  { icon: ShieldCheck, title: "Satisfaction guarantee", description: "Not happy with a service? We'll make it right." },
];

export function WhyChooseUs() {
  return (
    <Section>
      <SectionHeading eyebrow="Why choose us" title="Built for trust, from the ground up" />
      <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 lg:grid-cols-3">
        {points.map((point) => (
          <div key={point.title} className="flex gap-4">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--color-accent-light)] text-[var(--color-accent)]">
              <point.icon className="h-5 w-5" />
            </span>
            <div>
              <h3 className="font-semibold text-[var(--color-text-primary)]">{point.title}</h3>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">{point.description}</p>
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}
