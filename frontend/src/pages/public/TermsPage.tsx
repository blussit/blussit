import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "Our Service",
    body: [
      "Blussit provides doorstep vehicle cleaning and care at the address and time slot you choose.",
      "A booking is for a time slot, not an exact minute. Your captain will arrive within the selected slot.",
      "The price shown before confirmation is the price you pay, unless you add more services later.",
    ],
  },
  {
    title: "Your Responsibility",
    body: [
      "Please enter correct vehicle, address and contact details.",
      "Please keep the vehicle accessible at the booked time.",
      "Please remove valuables and loose items before the service starts.",
      "Please treat our captains with respect.",
    ],
  },
  {
    title: "Monthly Passes",
    body: [
      "A monthly pass is for one vehicle and one selected service.",
      "The pass price depends on the vehicle type and service you choose.",
      "Unused washes do not carry forward to the next month.",
      "Add-ons are not included in a pass. They are charged separately.",
    ],
  },
  {
    title: "Cancellations And Rescheduling",
    body: [
      "You can reschedule early for free.",
      "Cancellation rules are explained on the Cancellation Policy page.",
      "If a captain cannot access the vehicle after reaching your address, the visit may count as a late cancellation.",
    ],
  },
  {
    title: "Service Concerns",
    body: [
      "Captains take before and after photos for service proof.",
      "If you have a concern, raise it from your booking or contact us on WhatsApp.",
      "Our team will review the booking details and help resolve the issue.",
    ],
  },
];

export default function TermsPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PageSeo path="/terms" />
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Terms And Conditions</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">These terms explain how Blussit bookings, services and passes work.</p>
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
