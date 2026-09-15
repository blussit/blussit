import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";

/**
 * The published privacy policy — also a hard requirement for taking the
 * Meta WhatsApp app live. Plain, honest, matching what the product
 * actually collects and does.
 */
const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "What we collect",
    body: [
      "Your name, phone number and email address.",
      "Your vehicle details, such as type, brand, model and registration number.",
      "The address where you want the service.",
      "Booking history, payments recorded against bookings, subscriptions, reviews and support conversations.",
      "WhatsApp messages you send to Blussit, plus message delivery status from WhatsApp.",
      "Before and after photos of your vehicle for service proof.",
      "For captains, we collect identity documents and job-time location only while a job is active.",
    ],
  },
  {
    title: "How we use it",
    body: [
      "To create and manage your bookings.",
      "To send captains to the right address.",
      "To verify phone numbers with one-time passwords sent over WhatsApp (or SMS).",
      "To send booking updates, payment links, reminders and support replies.",
      "To improve service quality and resolve complaints.",
      "We do not sell your personal data.",
    ],
  },
  {
    title: "WhatsApp and service providers",
    body: [
      "When you message us or receive WhatsApp updates, WhatsApp Business Platform / Meta processes your phone number, message content and message status.",
      "Razorpay processes online payments. Blussit does not store your full card, UPI or bank details.",
      "Google Maps may help us find service addresses and estimate travel.",
      "Hosting and storage providers keep the app, database and uploaded photos working.",
      "We share data with these providers only to run Blussit's service.",
    ],
  },
  {
    title: "Retention and protection",
    body: [
      "We keep booking, payment and service records as long as needed for service, accounts, disputes and legal reasons.",
      "We may keep support and WhatsApp history so our team can understand previous requests.",
      "Captain job-time location is deleted after 30 days.",
      "Access to customer, captain, manager and admin data is role-based.",
    ],
  },
  {
    title: "Your choices",
    body: [
      "You can edit your vehicles, addresses and profile from your account at any time.",
      "You can ask us to delete your account or personal data by emailing contact.blussit@gmail.com, messaging us on WhatsApp, or using the contact form.",
      "You can ask us to stop WhatsApp service messages.",
      "We may still keep completed booking, payment or dispute records if required for accounts, tax, fraud prevention or legal reasons.",
    ],
  },
];

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PageSeo path="/privacy-policy" />
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Privacy Policy</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">
          Blussit uses your data only to run bookings, send updates, take payments and support you. We do not sell your personal data.
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
        <p className="mt-10 text-sm text-neutral-500">
          Questions about your data? Email contact.blussit@gmail.com, message us on WhatsApp, or use the contact form — we answer.
        </p>
      </main>
      <PublicFooter />
    </div>
  );
}
