import { useQuery } from "@tanstack/react-query";
import { Ban, Camera, CheckCircle2, ClipboardCheck, Wrench } from "lucide-react";
import { catalogApi } from "../../api/catalog";
import { parseIncludes, titleCase } from "../../components/public/landing/shared";
import { PolicyCard, PolicyPage, Points } from "./PolicyKit";

/**
 * What our wash does, what it doesn't, and what the customer keeps ready —
 * the rules the captain works to. The booking flow shows the lines that
 * apply to the wash being booked; this page is the full version.
 * Cancellation rules live only on /cancellation-policy.
 */
export default function ServicePolicyPage() {
  // The live catalogue, so this page never drifts from what admin sells:
  // one entry per main service (bike-count variants collapse to one) and
  // the add-ons underneath. No prices — this is the "what", not "how much".
  const { data: catalogue } = useQuery({ queryKey: ["public-services"], queryFn: () => catalogApi.services({ page_size: 100 }) });
  const services = (catalogue?.data || []).filter((s) => s.is_active !== false);
  const seenGroups = new Set<string>();
  const mains = services.filter((s) => {
    if (s.is_addon) return false;
    const key = s.variant_group || s.id;
    if (seenGroups.has(key)) return false;
    seenGroups.add(key);
    return true;
  });
  const addons = services.filter((s) => s.is_addon);
  const displayName = (s: { name: string; variant_group?: string | null }) => titleCase(s.variant_group ? s.name.split("(")[0].trim() : s.name);

  return (
    <PolicyPage
      path="/service-policy"
      title="Service Policy"
      intro="What each wash includes, what to keep ready, and what a wash can't fix."
      updated="7 October 2026"
    >
      {mains.length > 0 && (
        <PolicyCard icon={Wrench} title="What Each Service Includes">
          <ul className="grid gap-3 sm:grid-cols-2">
            {mains.map((s) => {
              const { summary, items } = parseIncludes(s.description);
              return (
                <li key={s.id} className="rounded-[14px] bg-[#F7F9FC] p-4">
                  <p className="font-display text-[16px] font-bold text-[#0E1A33]">
                    {displayName(s)}
                    {s.is_waterless && <span className="ml-2 rounded-full bg-white px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#0A66F0]">Waterless</span>}
                  </p>
                  {items.length > 0 ? (
                    <ul className="mt-2 space-y-1.5">
                      {items.map((item) => (
                        <li key={item} className="flex items-start gap-2 text-[14px]">
                          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" aria-hidden="true" /> {titleCase(item)}
                        </li>
                      ))}
                    </ul>
                  ) : summary ? (
                    <p className="mt-1.5 text-[14px]">{titleCase(summary)}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {addons.length > 0 && (
            <p className="mt-3 text-[14px]">
              <span className="font-semibold text-[#0E1A33]">Add-Ons You Can Book With Any Wash: </span>
              {addons.map((a) => titleCase(a.name)).join(", ")}.
            </p>
          )}
        </PolicyCard>
      )}

      <PolicyCard icon={ClipboardCheck} title="Keep Ready">
        <Points
          items={[
            "Park where the vehicle can be washed safely. Our captain doesn't move or drive it, and parking fees or fines are yours.",
            "Water-based washes need a water tap and a power point nearby. Without them the wash can't start, and the slot still counts as used.",
            "Waterless washes need a shaded or covered spot.",
            "Remove valuables and loose items.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={Ban} title="What A Wash Can't Do">
        <Points
          items={[
            "Remove scratches, dents or paint damage.",
            "Clean the inner wheel area in a waterless wash — book a water-based wash for that.",
            "Clean the parking floor around the vehicle.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={Camera} title="Photos And Concerns">
        <p>
          The captain takes before and after photos and adds them to your booking. Marks or damage already on the vehicle aren't caused by Blussit. If
          something looks wrong after a wash, raise it from your booking and our team will check it.
        </p>
      </PolicyCard>
    </PolicyPage>
  );
}
