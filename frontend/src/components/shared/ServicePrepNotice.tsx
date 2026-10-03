import { Info } from "lucide-react";
import type { Service } from "../../types";

/**
 * The one thing the customer must actually DO before the captain arrives —
 * a captain who turns up to no water is a wasted trip for both sides.
 *
 * Deliberately ONE line: this sits at the moment of paying, where a wall
 * of rules gets skipped rather than read. Parking, shade for a waterless
 * wash and what a wash covers live on /service-policy ("Read more").
 */
export function ServicePrepNotice({ services, className = "" }: { services: Service[]; className?: string }) {
  if (!services.some((s) => !s.is_addon)) return null;
  return (
    <p className={`flex items-start gap-2 rounded-xl border border-[#F3E5B5] bg-white p-3 text-xs leading-relaxed text-gray-600 ${className}`}>
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-black" aria-hidden="true" />
      <span>
        Please keep water and a power point near the vehicle.{" "}
        <a href="/service-policy" target="_blank" rel="noreferrer" className="font-semibold text-black underline underline-offset-2">
          Read More
        </a>
      </span>
    </p>
  );
}
