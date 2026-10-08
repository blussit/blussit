import { Building2, Mail, MapPin, Phone, MessageCircle } from "lucide-react";
import { BUSINESS } from "../../seo/content";

// Brand marks aren't in lucide (they were removed upstream) — two tiny
// inline glyphs, sized like the lucide icons beside them.
const InstagramIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <rect x="2" y="2" width="20" height="20" rx="5" />
    <circle cx="12" cy="12" r="4" />
    <circle cx="17.5" cy="6.5" r="0.8" fill="currentColor" stroke="none" />
  </svg>
);
const FacebookIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
    <path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z" />
  </svg>
);

const SOCIALS = [
  { label: "Chat with us on WhatsApp", href: `https://wa.me/${import.meta.env.VITE_WHATSAPP_NUMBER || "918962288774"}`, icon: MessageCircle },
  { label: "BLUSSIT on Instagram", href: "https://www.instagram.com/blussitwash/", icon: InstagramIcon },
  { label: "BLUSSIT on Facebook", href: "https://www.facebook.com/people/Blussit/61593594339671/", icon: FacebookIcon },
];

// Real pages, not /#section anchors: search engines ignore the part after
// "#", so these links are how they find (and rank) each service page.
const COLUMNS = [
  {
    title: "Services",
    links: [
      { label: "All Services", href: "/services" },
      { label: "Jet Wash", href: "/services/jet-wash" },
      { label: "Star Wash", href: "/services/star-wash" },
      { label: "Waterless Car Wash", href: "/services/waterless-car-wash" },
      { label: "Car Deep Cleaning", href: "/services/car-deep-cleaning" },
      { label: "Bike Wash", href: "/services/bike-wash" },
    ],
  },
  {
    title: "Plans",
    links: [
      { label: "Monthly Plans", href: "/plans" },
      { label: "Society Plans", href: "/plans#society" },
      { label: "Custom Plan", href: "/plans#custom-plan" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "Car Wash In Indore", href: "/doorstep-car-wash-indore" },
      { label: "How It Works", href: "/#how-it-works" },
      { label: "Book A Wash", href: "/book" },
    ],
  },
  {
    title: "Support",
    links: [
      { label: "Service Policy", href: "/service-policy" },
      { label: "Cancellation Policy", href: "/cancellation-policy" },
      { label: "Terms And Conditions", href: "/terms" },
      { label: "Privacy Policy", href: "/privacy-policy" },
    ],
  },
];

const CONTACT = [
  { icon: Phone, label: BUSINESS.phoneDisplay, href: BUSINESS.phoneHref },
  { icon: Mail, label: BUSINESS.email, href: `mailto:${BUSINESS.email}` },
  { icon: MapPin, label: BUSINESS.address, href: BUSINESS.mapsHref, external: true },
];

export function PublicFooter() {
  return (
    <footer className="border-t border-[#E4EBF5] bg-[#F7F9FC] text-[#0E1A33]">
      <div className="container-page">
        {/* Phones get a 2×2 table of link columns under the brand block
            instead of five stacked sections. */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-9 py-10 sm:py-14 lg:grid-cols-[1.6fr_1fr_1fr_1fr_1fr] lg:gap-8">
          {/* Brand */}
          <div className="col-span-2 lg:col-span-1 lg:pr-10">
            <a href="/" className="inline-flex items-center" aria-label="BLUSSIT Home">
              <img src="/img/blussit-logo-480.webp" width={480} height={63} alt="Blussit" className="h-[24px] w-auto object-contain" />
            </a>

            <p className="mt-4 max-w-[320px] text-[14px] leading-[1.6] text-[#5F6878]">
              Doorstep car and bike wash in every colony of Indore.
              <span className="block whitespace-nowrap">Open {BUSINESS.hours}.</span>
            </p>

            <a
              href="/plans#society"
              className="mt-5 inline-flex h-11 items-center gap-2 rounded-full bg-[#0A66F0] px-5 text-[14px] font-bold text-white shadow-[0_8px_20px_-6px_rgba(10,102,240,0.45)] transition-colors hover:bg-[#0857CF]"
            >
              <Building2 className="h-4 w-4" aria-hidden="true" /> Society Plans
            </a>

            <div className="mt-5 flex items-center gap-2.5">
              {SOCIALS.map((social) => (
                <a
                  key={social.label}
                  href={social.href}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={social.label}
                  className="flex h-9 w-9 items-center justify-center rounded-full border border-[#D6DEEA] bg-white text-[#0E1A33] transition-colors hover:border-[#0A66F0] hover:bg-[#0A66F0] hover:text-white"
                >
                  <social.icon className="h-4 w-4" />
                </a>
              ))}
            </div>
          </div>

          {/* Columns */}
          {COLUMNS.map((column) => (
            <div key={column.title}>
              <h3 className="mb-3 font-display text-[14px] font-bold sm:mb-4">{column.title}</h3>
              <ul className="space-y-2.5">
                {column.links.map((link) => (
                  <li key={link.label}>
                    <a href={link.href} className="text-[14px] leading-5 text-[#5F6878] transition-colors hover:text-[#0A66F0]">
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* Bottom */}
        <div className="flex flex-col gap-4 border-t border-[#E4EBF5] py-6 md:flex-row md:items-center md:justify-between">
          <ul className="flex flex-col gap-2.5 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-7">
            {CONTACT.map(({ icon: Icon, label, href, external }) => (
              <li key={label}>
                <a
                  href={href}
                  {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  className="flex items-center gap-2 text-[13.5px] font-medium transition-colors hover:text-[#0A66F0]"
                >
                  <Icon className="h-4 w-4 shrink-0 text-[#0A66F0]" aria-hidden="true" />
                  {label}
                </a>
              </li>
            ))}
          </ul>

          {/* rel="noopener noreferrer" because the credit opens in a new tab. */}
          <p className="text-[12.5px] text-[#8A94A6]">
            © {new Date().getFullYear()} BLUSSIT. All rights reserved.{" "}
            <span className="whitespace-nowrap">
              Developed by{" "}
              <a
                href="https://kalakartechcrew.online"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 transition-colors hover:text-[#0E1A33]"
              >
                Kalakartechcrew
              </a>
            </span>
          </p>
        </div>
      </div>
    </footer>
  );
}
