import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";

/**
 * The published privacy policy — also a hard requirement for taking the
 * Meta WhatsApp app live. Plain, honest, matching what the product
 * actually collects and does.
 */
const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "What we collect",
    body: [
      "Your name, phone number and email address (for your account and to reach you about bookings).",
      "Your vehicle details (type, brand, model, registration number) and the addresses you ask us to serve.",
      "Booking history, payments recorded against bookings, subscriptions, reviews and support conversations.",
      "For our field captains: identity documents you submit for verification, and job-time location captured only while a job is active.",
      "Photos of your vehicle taken by the captain before and after every service, as proof of work.",
    ],
  },
  {
    title: "How we use it",
    body: [
      "To run your bookings end to end: dispatching the right team, telling you who's coming, and keeping you updated on WhatsApp.",
      "To verify phone numbers with one-time passwords sent over WhatsApp (or SMS).",
      "To improve service quality — reviews, complaint handling, and operational metrics are derived from booking records.",
      "We never sell your personal data, and we don't share it with anyone except the service providers who make the product work (messaging, maps, hosting).",
    ],
  },
  {
    title: "Third-party services",
    body: [
      "WhatsApp (Meta) — booking updates, OTPs and support conversations are delivered through the WhatsApp Business API.",
      "Google Maps — address pinning, coverage checks and travel estimates.",
      "Our cloud hosting and database providers store the data described above.",
    ],
  },
  {
    title: "Your choices",
    body: [
      "You can edit your vehicles, addresses and profile from your account at any time.",
      "You can ask us to delete your account — write to us on WhatsApp or through the contact form. Records of completed bookings may be retained where required for accounting.",
      "Location from captains is captured only during active jobs and automatically deleted after 30 days.",
    ],
  },
];

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-white text-black">
      <PublicNavbar />
      <main className="container-page max-w-[820px] py-12 sm:py-16">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#B08A00]">Policy</p>
        <h1 className="mt-1 font-display text-3xl font-bold">Privacy Policy</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-neutral-600">
          Blussit exists to wash vehicles at your doorstep — not to trade in your data. This page says plainly what we
          collect, why, and what happens to it.
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
          Questions about your data? Message us on WhatsApp or use the contact form — we answer.
        </p>
      </main>
      <PublicFooter />
    </div>
  );
}
