import { type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Droplets, ParkingCircle, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { catalogApi } from "../../api/catalog";
import { parseIncludes, titleCase } from "../../components/public/landing/shared";

/**
 * What our wash does, what it doesn't, and what the customer has to have
 * ready. Everything here is an operational rule the captain works to — the
 * booking flow shows the two or three lines that apply to the wash being
 * booked, and this page is the full version behind that link.
 *
 * Cancellation rules live on /cancellation-policy and are deliberately NOT
 * repeated here.
 */
export default function ServicePolicyPage() {
  // The live catalogue, so this page never drifts from what admin sells:
  // one entry per main service (bike-count variants collapse to one) and
  // the add-ons underneath. What each includes comes from the service's
  // own description — no prices here, this is the "what", not the "how
  // much".
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

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />

      <main className="container-page py-12 sm:py-16">
        <div className="mx-auto max-w-2xl">
          <h1 className="font-display text-3xl font-bold text-black sm:text-4xl">Service Policy</h1>
          <p className="mt-2 text-[15px] text-neutral-600">
            This page explains what we clean, what we do not clean, and what you need to keep ready.
          </p>

          {mains.length > 0 && (
            <Section icon={Wrench} title="What Each Service Includes">
              <p>Every wash is done at your doorstep by a trained captain. We add before and after photos to your booking.</p>
              <ul className="mt-3 space-y-4">
                {mains.map((s) => {
                  const { summary, items } = parseIncludes(s.description);
                  return (
                    <li key={s.id} className="rounded-xl border border-[#F3E5B5] bg-[#FFFCF0] p-4">
                      <p className="font-display text-base font-bold text-black">
                        {displayName(s)}
                        {s.is_waterless && <span className="ml-2 rounded-full bg-white px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#B08A00]">Waterless</span>}
                      </p>
                      {items.length > 0 ? (
                        <ul className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                          {items.map((item) => (
                            <li key={item} className="flex items-start gap-2 text-sm text-neutral-700">
                              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#B08A00]" /> {titleCase(item)}
                            </li>
                          ))}
                        </ul>
                      ) : summary ? (
                        <p className="mt-1.5 text-sm text-neutral-700">{titleCase(summary)}</p>
                      ) : (
                        <p className="mt-1.5 text-sm text-neutral-500">Details Coming Soon.</p>
                      )}
                    </li>
                  );
                })}
              </ul>
              {addons.length > 0 && (
                <p className="mt-3 text-sm">
                  <span className="font-semibold text-black">Extras you can add to any wash: </span>
                  {addons.map((a) => titleCase(a.name)).join(", ")}.
                </p>
              )}
            </Section>
          )}

          <Section icon={ParkingCircle} title="Parking Is Your Responsibility">
            <p>
              Please park the vehicle in a place where it can be washed safely. Our captain cleans the vehicle where it is parked.
            </p>
            <p>
              Our captain does not move, drive or re-park your vehicle. Parking fees, tickets, towing, clamping and building disputes are the customer's responsibility.
            </p>
          </Section>

          <Section icon={Droplets} title="Water-Based Washes">
            <p>
              For Jet Wash, Foam Wash, Star Wash, Deep Cleaning and Bike Wash, please keep water and electricity ready.
            </p>
            <p>
              If water or electricity is not available when the captain arrives, the service may not start and the slot will still count as booked.
            </p>
          </Section>

          <Section icon={Sparkles} title="Waterless Washes">
            <p>
              A waterless wash needs a shaded or covered parking spot. Direct sunlight can dry the product too quickly.
            </p>
            <p>
              A waterless wash does not clean the inner wheel area. Please book a water-based wash if you need that area cleaned.
            </p>
          </Section>

          <Section icon={Sparkles} title="What A Wash Does Not Do">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>A wash does not remove scratches, dents or paint damage.</li>
              <li>A wash does not clean the parking floor or surrounding area.</li>
              <li>Please remove valuables and loose items before the service starts.</li>
            </ul>
          </Section>

          <Section icon={ShieldCheck} title="Vehicle Condition">
            <p>
              Your captain takes photos before and after the service. These photos are attached to your booking.
            </p>
            <p>
              Existing marks, scratches, damage or missing items are not caused by Blussit.
            </p>
            <p>
              If you think something happened during the service, raise a support request from your booking. Our team will review it.
            </p>
          </Section>

          <p className="mt-10 text-sm text-neutral-600">
            Cancellations and rescheduling are explained in our{" "}
            <Link to="/cancellation-policy" className="font-semibold text-black underline underline-offset-2">
              Cancellation Policy
            </Link>
            .
          </p>
        </div>
      </main>

      <PublicFooter />
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof ParkingCircle;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-10">
      <div className="flex items-center gap-2.5">
        <Icon className="h-5 w-5 shrink-0 text-[#B08A00]" />
        <h2 className="font-display text-lg font-bold text-black">{title}</h2>
      </div>
      <div className="mt-2.5 space-y-2.5 text-[15px] leading-relaxed text-neutral-700">{children}</div>
    </section>
  );
}
