import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "The service",
    body: [
      "Blussit provides doorstep vehicle cleaning and care at the address and time slot you book.",
      "A booking is confirmed for a time slot, not an exact minute — your captain arrives within the slot you chose.",
      "Prices shown at booking time are what you pay; any add-ons you select are charged in addition and shown before you confirm.",
    ],
  },
  {
    title: "Your responsibilities",
    body: [
      "Provide accurate vehicle and address details, and make the vehicle accessible at the booked time.",
      "First-time offer pricing applies once per vehicle and phone number, across accounts.",
      "Treat our captains with respect — they photograph the vehicle before and after service for everyone's protection.",
    ],
  },
  {
    title: "Cancellations & rescheduling",
    body: [
      "Online cancellation closes 4 hours before your slot; the full schedule of cancellation terms is published on our Cancellation Policy page.",
      "Rescheduling releases your captain — a new one is assigned for the new time.",
    ],
  },
  {
    title: "Subscriptions",
    body: [
      "A plan is purchased for a vehicle type and can be redeemed on that type or a smaller one, never a bigger one.",
      "Each visit uses one plan credit; picking a cheaper service still uses one full credit. Add-ons are always charged separately.",
      "Plans expire at the end of their cycle; unused visits don't carry over.",
    ],
  },
  {
    title: "Liability",
    body: [
      "We take care of your vehicle as if it were ours, and before/after photos document its condition at each service.",
      "Raise any service concern through the in-app support (tied to the booking) — the serving branch manager is accountable for resolving it.",
    ],
  },
];

export default function TermsPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Terms &amp; Conditions</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">
          The short version: we show up, we do the work properly, and we're straight with you about pricing. The details:
        </p>
        <div className="mt-8 space-y-8">
          {SECTIONS.map((s) => (
            <section key={s.title}>
              <h2 className="font-display text-xl font-bold">{s.title}</h2>
              <ul className="mt-3 space-y-2">
                {s.body.map((line) => (
                  <li key={line} className="flex gap-2.5 text-[15px] leading-relaxed text-neutral-700">
                    <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#E8A900]" />
                    {line}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}
