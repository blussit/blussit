import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { AlertTriangle, CheckCircle2, Clock, ParkingCircle } from "lucide-react";

/**
 * The published cancellation policy. The 4-hour online-cancellation lock is
 * live in the product today; the charge tiers below are the stated policy
 * and are introduced progressively (charges are added to the next booking).
 */
export default function CancellationPolicyPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Cancellation Policy</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">
          Every slot we confirm reserves a captain, equipment and travel time for your doorstep. Cancelling late means that
          capacity goes to waste instead of serving another customer — this policy keeps things fair for everyone.
        </p>

        <div className="mt-8 space-y-4">
          <section className="rounded-2xl border border-[#EDE6D6] bg-[#FFFCF5] p-5">
            <div className="flex items-center gap-2.5">
              <CheckCircle2 className="h-5 w-5 text-green-600" />
              <h2 className="font-display text-lg font-bold">Free cancellation — up to 4 hours before your slot</h2>
            </div>
            <p className="mt-2 text-sm leading-relaxed text-neutral-700">
              You can cancel any booking free of charge from the app or website any time until 4 hours before your slot
              starts, as long as a captain hasn't been assigned yet.
            </p>
          </section>

          <section className="rounded-2xl border border-[#EDE6D6] bg-white p-5">
            <div className="flex items-center gap-2.5">
              <Clock className="h-5 w-5 text-[#B08A00]" />
              <h2 className="font-display text-lg font-bold">Within 4 hours of your slot</h2>
            </div>
            <ul className="mt-3 space-y-2.5 text-sm leading-relaxed text-neutral-700">
              <li>
                <span className="font-semibold text-black">1–4 hours before the slot:</span> online cancellation is closed.
                A first-time cancellation in this window carries a warning, and the account moves to
                <span className="font-semibold"> prepaid-only booking</span> (pay while booking, no pay-after-service). Repeat
                cancellations in this window carry a <span className="font-semibold">₹50 charge</span>.
              </li>
              <li>
                <span className="font-semibold text-black">Less than 1 hour before the slot:</span> a
                <span className="font-semibold"> ₹50 charge</span> applies (₹80 for repeat cancellations), and the account
                moves to prepaid-only booking.
              </li>
              <li>
                <span className="font-semibold text-black">After the captain has started the ride:</span> a
                <span className="font-semibold"> ₹100 charge</span> applies.
              </li>
            </ul>
          </section>

          <section className="rounded-2xl border border-[#EDE6D6] bg-white p-5">
            <div className="flex items-center gap-2.5">
              <AlertTriangle className="h-5 w-5 text-[#B08A00]" />
              <h2 className="font-display text-lg font-bold">How charges are collected</h2>
            </div>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed text-neutral-700">
              <li>Any cancellation charge is added to your next booking's bill — nothing is charged on the spot.</li>
              <li>
                Repeated late cancellations follow a simple ladder: first time — a warning and prepaid-only booking; second
                time — booking requires prepayment; from the third time — the charges above apply every time.
              </li>
              <li>Rescheduling in good time is always free — if your plan changes, move the slot instead of cancelling.</li>
              <li>Need help with a booking inside the 4-hour window? Message us on WhatsApp and our team will sort it out.</li>
            </ul>
          </section>
          {/* Parking is the customer's own arrangement — stated plainly so
              there is no argument at the door about tickets, towing or a
              spot the captain cannot reach. */}
          <section className="rounded-2xl border border-[#EDE6D6] bg-white p-5">
            <div className="flex items-center gap-2.5">
              <ParkingCircle className="h-5 w-5 text-[#B08A00]" />
              <h2 className="font-display text-lg font-bold">Parking is your responsibility</h2>
            </div>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed text-neutral-700">
              <li>
                Please park the vehicle yourself, in a spot where it can be washed. Our captain services the vehicle where
                you have parked it — we do not move, drive or re-park it at any time.
              </li>
              <li>
                Blussit is not responsible for parking fees, parking tickets, clamping, towing or any dispute with the
                society, building or authority over where the vehicle is parked. What a wash covers, and what you need
                ready before we arrive, is set out in our <a href="/service-policy" className="font-semibold text-black underline underline-offset-2">service policy</a>.
              </li>
              <li>
                If the captain reaches your address and the vehicle is unreachable — locked basement, no access, or no
                usable space to work in — the visit counts as a late cancellation.
              </li>
            </ul>
          </section>
        </div>

        <p className="mt-8 text-xs text-neutral-500">
          Blussit may update this policy from time to time; the version on this page is the one in effect.
        </p>
      </main>
      <PublicFooter />
    </div>
  );
}
