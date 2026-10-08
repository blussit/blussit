import { BarChart3, Clock, Database, Settings2, Share2, Target } from "lucide-react";
import { BUSINESS } from "../../seo/content";
import { PolicyCard, PolicyPage, Points } from "./PolicyKit";

/**
 * The published privacy policy — also a hard requirement for taking the
 * Meta WhatsApp app live. Plain and honest: it must match what the product
 * actually collects and who it actually shares it with.
 */
export default function PrivacyPolicyPage() {
  return (
    <PolicyPage
      path="/privacy-policy"
      title="Privacy Policy"
      intro="We use your details only to run your bookings, keep you updated and support you. We never sell your personal data."
      updated="7 October 2026"
    >
      <PolicyCard icon={Database} title="What We Collect">
        <Points
          items={[
            "Your name, phone number and email.",
            "Your vehicles and service addresses.",
            "Your bookings, payments, plans, reviews and support chats, including WhatsApp messages you send us.",
            "Before and after photos of your vehicle.",
            "For captains: ID documents, and location only while a job is active.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={Target} title="Why We Use It">
        <Points
          items={[
            "To book your wash and send the captain to the right place.",
            "To send login codes (OTP), booking updates, reminders and payment links on WhatsApp or SMS.",
            "To sort out complaints and improve our service.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={Share2} title="Who We Share It With">
        <p className="mb-2">Only the services we need to run Blussit:</p>
        <Points
          items={[
            "WhatsApp (Meta) — to deliver our messages.",
            "Razorpay — for online payments. We never see or store your full card, UPI or bank details.",
            "Google — for maps and address lookup, and for sign-in if you choose Sign In With Google.",
            "Our hosting and storage providers — to keep the app, data and photos running.",
          ]}
        />
      </PolicyCard>

      <PolicyCard icon={BarChart3} title="Website Analytics">
        <p>
          Our website uses Google Analytics and the Meta Pixel to count visits and see which ads bring customers. They use cookies; you can block or
          clear them in your browser settings.
        </p>
      </PolicyCard>

      <PolicyCard icon={Clock} title="How Long We Keep It">
        <p>
          We keep booking and payment records as long as needed for service, accounts, tax and legal reasons. Captain job location is deleted after 30
          days.
        </p>
      </PolicyCard>

      <PolicyCard icon={Settings2} title="Your Choices">
        <Points
          items={[
            "Edit your profile, vehicles and addresses in your account at any time.",
            `To delete your account or data, or to stop WhatsApp messages, email ${BUSINESS.email} or message us on WhatsApp.`,
            "Some completed booking and payment records may be kept where the law requires it.",
          ]}
        />
      </PolicyCard>
    </PolicyPage>
  );
}
