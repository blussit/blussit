import type { ReactNode } from "react";
import { Check, ChevronRight } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * The booking wizard shell — the onboarding layout the founder picked as
 * the reference: a quiet numbered rail on the left (ticks for what's
 * done, the current step in black, the rest greyed), ONE narrow centred
 * column of fields, and a single primary action pinned to the bottom of
 * the card. No third column, no decorative panels: the price lives
 * directly above the action where it's about to be agreed to.
 *
 * Used by BOTH booking entry points — the public/guest wizard and the
 * logged-in one — so before and after login look like the same product.
 */
export function WizardShell({
  eyebrow,
  title,
  steps,
  current,
  onStepClick,
  aside,
  footer,
  children,
}: {
  eyebrow?: string;
  title: string;
  steps: string[];
  current: number;
  /** Completed steps are clickable to go back and change an answer. */
  onStepClick?: (index: number) => void;
  /** Optional small block under the rail (e.g. a support line). */
  aside?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-5xl">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr] lg:gap-10">
        {/* Rail — a real list on desktop, a compact tick strip on phones. */}
        <div className="lg:sticky lg:top-24 lg:self-start">
          <div className="hidden lg:block">
            {eyebrow && <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">{eyebrow}</p>}
            <h1 className="mt-1 font-display text-xl font-bold leading-tight text-black">{title}</h1>
            <ol className="mt-6 space-y-1">
              {steps.map((label, i) => {
                const done = i < current;
                const active = i === current;
                const clickable = done && !!onStepClick;
                return (
                  <li key={label}>
                    <button
                      type="button"
                      disabled={!clickable}
                      onClick={() => clickable && onStepClick(i)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors",
                        clickable && "hover:bg-[#FFFCF0]",
                        !clickable && "cursor-default"
                      )}
                    >
                      <span
                        className={cn(
                          "font-mono-num flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                          done ? "bg-[#E8A900] text-white" : active ? "bg-black text-white" : "border border-[#F3E5B5] text-gray-400"
                        )}
                      >
                        {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                      </span>
                      <span className={cn("flex-1 text-sm", active ? "font-semibold text-black" : done ? "text-gray-600" : "text-gray-400")}>
                        {label}
                      </span>
                      {clickable && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-gray-300" />}
                    </button>
                  </li>
                );
              })}
            </ol>
            {aside && <div className="mt-6">{aside}</div>}
          </div>

          {/* Mobile: title + a tick strip, nothing else. */}
          <div className="lg:hidden">
            <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">
              Step {current + 1} of {steps.length} · {steps[current]}
            </p>
            <h1 className="mt-1 font-display text-2xl font-bold text-black">{title}</h1>
            <div className="mt-3 flex items-center gap-1.5">
              {steps.map((label, i) => (
                <span
                  key={label}
                  className={cn(
                    "h-1 flex-1 rounded-full transition-colors",
                    i < current ? "bg-[#E8A900]" : i === current ? "bg-black" : "bg-[#F3E5B5]"
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        {/* One column of fields, with the action pinned under them. */}
        <div className="min-w-0">
          <div className="rounded-2xl border border-[#F3E5B5] bg-white">
            <div className="p-5 sm:p-6">{children}</div>
            {footer && <div className="border-t border-[#F3E5B5] p-5 sm:p-6">{footer}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The one-line heading inside a wizard step — matches the reference's
 * "Add your Bank details" + one sentence of context.
 */
export function WizardStepHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-5">
      <h2 className="font-display text-lg font-bold text-black sm:text-xl">{title}</h2>
      {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
    </div>
  );
}
