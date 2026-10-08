import { Link } from "react-router-dom";
import { CalendarCheck, CalendarX, Camera, CreditCard, Repeat, UserCheck } from "lucide-react";
import { PolicyCard, PolicyPage, Points } from "./PolicyKit";

const policyLink = (to: string, label: string) => (
  <Link to={to} className="font-semibold text-[#0A66F0] underline underline-offset-2">
    {label}
  </Link>
);

export default function TermsPage() {
  return (
    <PolicyPage
      path="/terms"
      title="Terms And Conditions"
      intro="The simple rules for booking a Blussit wash. By booking, you agree to them."
      updated="7 October 2026"
    >
      <PolicyCard icon={CalendarCheck} title="Booking">
        <Points
          items={[
            "You choose the service, the address and a time slot. The captain arrives within that slot.",
            "The price shown before you confirm is the price you pay. Any distance charge or add-on is shown before you confirm.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={CreditCard} title="Payment">
        <Points
          items={[
            "Pay online while booking, or pay the captain after the wash where the booking page allows it.",
            "Some offer prices, and monthly plans bought on the website, are paid online.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={UserCheck} title="What We Need From You">
        <Points
          items={[
            "The right address and phone number.",
            "Your vehicle parked where it can be washed, with water and power nearby for water-based washes.",
            "Valuables removed from the vehicle before the wash.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={CalendarX} title="Cancellation">
        <p>Free up to 4 hours before your slot. Later than that, a small amount is deducted — see our {policyLink("/cancellation-policy", "Cancellation Policy")}.</p>
      </PolicyCard>

      <PolicyCard icon={Repeat} title="Monthly Plans">
        <Points
          items={[
            "One plan covers one vehicle for one month.",
            "Unused washes don't carry over to the next month.",
            "Add-ons are paid separately.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={Camera} title="If Something Is Wrong">
        <p>
          Our captain takes before and after photos of every wash. Raise a concern from your booking or on WhatsApp and our team will check it with
          you. Scratches or damage that were already on the vehicle aren't covered — see our {policyLink("/service-policy", "Service Policy")}.
        </p>
      </PolicyCard>
    </PolicyPage>
  );
}
