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
    title: "Monthly passes",
    body: [
      "A pass is bought for ONE named vehicle and covers ONE service, chosen at purchase. Its price depends on that vehicle's type and that service.",
      "One vehicle carries one pass at a time. You can hold passes for as many of your vehicles as you like.",
      "The pass covers only the service it was bought for, on the vehicle it was bought for. Anything else is booked and paid for normally.",
      "Each visit uses one wash from the pass. Add-ons (polish, extra bike washes and similar) are never covered and are paid online with that booking.",
      "Passes are monthly and are paid online only — there is no cash option for buying a pass or for the add-ons on a pass booking.",
      "With auto-pay on, the pass renews itself each month at the same price until you turn it off; you can turn it off at any time and keep the washes you have already paid for.",
      "A pass runs for its month; unused washes don't carry over into the next one.",
      "Need something these passes don't cover — more vehicles, more washes or a fixed weekly time? Request a custom plan and we'll price it for you.",
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
