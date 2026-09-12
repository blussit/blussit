import { type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Droplets, ParkingCircle, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { catalogApi } from "../../api/catalog";
import { parseIncludes } from "../../components/public/landing/shared";

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
  const displayName = (s: { name: string; variant_group?: string | null }) => (s.variant_group ? s.name.split("(")[0].trim() : s.name);

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />

      <main className="container-page py-12 sm:py-16">
        <div className="mx-auto max-w-2xl">
          <h1 className="font-display text-3xl font-bold text-black sm:text-4xl">Service policy</h1>
          <p className="mt-2 text-[15px] text-neutral-600">
            What to have ready before we arrive, and what a wash covers.
          </p>

          {mains.length > 0 && (
            <Section icon={Wrench} title="What each service includes">
              <p>Every wash is done at your doorstep by a trained captain, with before and after photos on your booking.</p>
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
                              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#B08A00]" /> {item}
                            </li>
                          ))}
                        </ul>
                      ) : summary ? (
                        <p className="mt-1.5 text-sm text-neutral-700">{summary}</p>
                      ) : (
                        <p className="mt-1.5 text-sm text-neutral-500">Details coming soon.</p>
                      )}
                    </li>
                  );
                })}
              </ul>
              {addons.length > 0 && (
                <p className="mt-3 text-sm">
                  <span className="font-semibold text-black">Extras you can add to any wash: </span>
                  {addons.map((a) => a.name).join(", ")}.
                </p>
              )}
            </Section>
          )}

          <Section icon={ParkingCircle} title="Parking is yours to arrange">
            <p>
              Please park the vehicle yourself, in a spot where it can be washed. Our captain services the vehicle
              exactly where you have parked it — we never move, drive or re-park it.
            </p>
            <p>
              Parking fees, tickets, clamping, towing and any dispute with your society, building or local authority
              over where the vehicle is parked remain yours.
            </p>
          </Section>

          <Section icon={Droplets} title="Water-based washes: water and power from you">
            <p>
              For any wash that uses water — jet wash, foam wash, star wash, deep cleaning, bike wash — the water and
              the electricity come from your supply.
            </p>
            <p>
              Please have both available and reachable at the vehicle <strong>by the time the captain arrives</strong>.
              A captain who arrives to no water can't start the wash, and the slot is held for the booking either way.
            </p>
          </Section>

          <Section icon={Sparkles} title="Waterless washes: park in the shade">
            <p>
              A waterless wash uses a chemical rather than running water, so the vehicle needs to be parked in a shaded
              or covered spot. Direct sun makes the product dry on the paint before it can be worked off.
            </p>
            <p>
              The inner wheel area cannot be cleaned in a waterless wash — the chemical isn't made for it. Book a
              water-based wash if that's what you need done.
            </p>
          </Section>

          <Section icon={Sparkles} title="What a wash does not do">
            <ul className="list-disc space-y-1.5 pl-5">
              <li>
                <strong>Scratches are not removed.</strong> A wash cleans the vehicle; it does not repair, polish out or
                touch up scratches, dents or paint damage.
              </li>
              <li>
                <strong>The surrounding area is not cleaned.</strong> Mud, dust and dirt coming off the vehicle are not
                collected, and the parking spot or floor around it is not washed down afterwards.
              </li>
            </ul>
          </Section>

          <Section icon={ShieldCheck} title="The vehicle's existing condition">
            <p>
              Your captain photographs the vehicle before starting and after finishing, and both photos are attached to
              your booking. Those photos are the record of what the vehicle looked like when we arrived.
            </p>
            <p>
              Damage, marks or missing items that were already there before the service are not ours, inside or out.
              Please remove valuables and loose items from the vehicle before the captain arrives.
            </p>
            <p>
              If you believe something happened <em>during</em> the service, raise it from the booking in your account —
              it goes to the manager of the branch that served you, with the before and after photos attached.
            </p>
          </Section>

          <p className="mt-10 text-sm text-neutral-600">
            Cancellations and rescheduling are covered separately in our{" "}
            <Link to="/cancellation-policy" className="font-semibold text-black underline underline-offset-2">
              cancellation policy
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
