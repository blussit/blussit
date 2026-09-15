import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";
import { AlertTriangle, CheckCircle2, Clock, ParkingCircle } from "lucide-react";

export default function CancellationPolicyPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PageSeo path="/cancellation-policy" />
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Cancellation Policy</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">
          A confirmed booking reserves a captain, equipment and travel time. Please cancel or reschedule early so the slot can be used by another customer.
        </p>

        <div className="mt-8 space-y-4">
          <Section icon={CheckCircle2} title="Free Cancellation">
            <p>You can cancel free of charge up to 4 hours before your slot, as long as a captain has not been assigned.</p>
          </Section>

          <Section icon={Clock} title="Late Cancellation">
            <ul className="space-y-2">
              <li>1 to 4 hours before the slot: online cancellation is closed. The first time is a warning. Repeat late cancellations may add a ₹50 charge.</li>
              <li>Less than 1 hour before the slot: a ₹50 charge may apply. Repeat late cancellations may add an ₹80 charge.</li>
              <li>After the captain starts travelling: a ₹100 charge may apply.</li>
            </ul>
          </Section>

          <Section icon={AlertTriangle} title="How Charges Work">
            <ul className="space-y-2">
              <li>Any cancellation charge is added to your next booking. We do not charge it separately on the spot.</li>
              <li>Rescheduling early is free. If your plan changes, please reschedule instead of cancelling late.</li>
              <li>If you need help inside the 4-hour window, message us on WhatsApp.</li>
            </ul>
          </Section>

          <Section icon={ParkingCircle} title="Parking Responsibility">
            <ul className="space-y-2">
              <li>Please park your vehicle in a place where it can be washed safely.</li>
              <li>Our captain does not move, drive or re-park your vehicle.</li>
              <li>Parking fees, tickets, towing, clamping or society/building disputes are the customer's responsibility.</li>
              <li>If the captain reaches the address and cannot access the vehicle, the visit may count as a late cancellation.</li>
            </ul>
          </Section>
        </div>

        <p className="mt-8 text-xs text-neutral-500">Blussit may update this policy when needed. The latest version is always shown on this page.</p>
      </main>
      <PublicFooter />
    </div>
  );
}

function Section({ icon: Icon, title, children }: { icon: typeof CheckCircle2; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-[#EDE6D6] bg-white p-5">
      <div className="flex items-center gap-2.5">
        <Icon className="h-5 w-5 text-[#B08A00]" />
        <h2 className="font-display text-lg font-bold">{title}</h2>
      </div>
      <div className="mt-2 text-sm leading-relaxed text-neutral-700">{children}</div>
    </section>
  );
}
