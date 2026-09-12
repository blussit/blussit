import { ArrowUpRight, Info } from "lucide-react";
import type { Service } from "../../types";

/**
 * The one thing the customer must actually DO before the captain arrives,
 * picked for the wash they're booking — a captain who turns up to no water
 * (or to a car baking in the sun) is a wasted trip for both sides.
 *
 * Deliberately ONE line: this sits at the moment of paying, where a wall
 * of rules gets skipped rather than read. Everything else — parking, what
 * a wash does and doesn't cover — lives on /service-policy, and "Read
 * more" goes straight there rather than unfolding it here.
 */
export function ServicePrepNotice({ services, className = "" }: { services: Service[]; className?: string }) {
  const main = services.filter((s) => !s.is_addon);
  if (!main.length) return null;

  // One line, the same for every wash (founder call): the captain needs
  // water and a power point at the vehicle. Everything else — shade for a
  // waterless wash, parking, what a wash covers — is on the policy page.
  const lead = "Please keep water and an electricity point accessible at the vehicle while we wash.";

  return (
    <div className={`rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-3.5 ${className}`}>
      <p className="flex items-center gap-1.5 text-sm font-semibold text-black">
        <Info className="h-4 w-4 text-[#B08A00]" /> Before we arrive
      </p>
      <p className="mt-1.5 text-xs leading-relaxed text-gray-700">{lead}</p>
      <a
        href="/service-policy"
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-flex items-center gap-0.5 text-xs font-semibold text-black underline underline-offset-2"
      >
        Read more <ArrowUpRight className="h-3 w-3" />
      </a>
    </div>
  );
}
