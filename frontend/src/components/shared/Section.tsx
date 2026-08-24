import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export function Section({ id, className, children }: { id?: string; className?: string; children: ReactNode }) {
  return (
    <section id={id} className={cn("py-16 md:py-24", className)}>
      <div className="container-page">{children}</div>
    </section>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  align = "center",
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  align?: "center" | "left";
}) {
  return (
    <div className={cn("mb-12 max-w-2xl", align === "center" ? "mx-auto text-center" : "text-left")}>
      {eyebrow && (
        <span className="mb-3 inline-block rounded-full bg-[var(--color-primary-light)] px-3 py-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-primary)]">
          {eyebrow}
        </span>
      )}
      <h2 className="text-3xl font-bold text-[var(--color-text-primary)] md:text-4xl">{title}</h2>
      {description && <p className="mt-4 text-base text-[var(--color-text-secondary)] md:text-lg">{description}</p>}
    </div>
  );
}
