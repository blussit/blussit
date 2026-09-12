import {
  Mail,
  MapPin,
  Phone,
  MessageCircle,
} from "lucide-react";

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

const COLUMNS = [
  {
    title: "Services",
    links: [
      { label: "All Services", href: "/services" },
      { label: "Exterior Clean", href: "/services" },
      { label: "Interior Clean", href: "/services" },
      { label: "Premium Care", href: "/services" },
      { label: "Other Services", href: "/services" },
    ],
  },
  {
    title: "Plans",
    links: [
      // Three links all pointing at /plans, two of them naming products we
      // don't sell (yearly plans are retired; "corporate" was never a
      // thing). Two honest links instead.
      { label: "Monthly passes", href: "/plans" },
      { label: "Custom plan", href: "/plans" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About Us", href: "/#how-it-works" },
      { label: "How It Works", href: "/#how-it-works" },
    ],
  },
  {
    title: "Support",
    links: [
      { label: "Service Policy", href: "/service-policy" },
      { label: "Cancellation Policy", href: "/cancellation-policy" },
      { label: "Terms & Conditions", href: "/terms" },
      { label: "Privacy Policy", href: "/privacy-policy" },
    ],
  },
];

export function PublicFooter() {
  return (
    <footer className="border-t border-black/[0.07] bg-white text-[#111111]">

      <div className="container-page">

        {/* Main footer. Phones get a compact 2×2 table of link columns
            under the brand block instead of five stacked sections — the
            old single column ran longer than most pages it sat under. */}
        <div className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 sm:py-12 lg:grid-cols-[1.8fr_1fr_1fr_1fr_1fr] lg:gap-8">

          {/* Brand */}
          <div className="col-span-2 lg:col-span-1 lg:pr-12">

            <a
              href="/"
              className="inline-flex items-center"
              aria-label="BLUSSIT Home"
            >
              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="h-[23px] w-auto object-contain"
              />
            </a>

            <p className="mt-4 text-[10px] font-semibold uppercase tracking-[0.18em] text-[#777777]">
              Premium Doorstep Vehicle Care
            </p>

            <p className="mt-2.5 max-w-[300px] text-[12px] leading-[1.7] text-[#707070]">
              Your car. Our care. Anywhere. Experience the ultimate
              convenience in vehicle maintenance.
            </p>

            {/* Social icons — only channels that actually exist. */}
            <div className="mt-5 flex items-center gap-2.5">
              {SOCIALS.map((social) => (
                <a
                  key={social.label}
                  href={social.href}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={social.label}
                  className="
                    flex h-8 w-8 items-center justify-center
                    rounded-full
                    border border-[#DDDDDD]
                    text-[#555555]
                    transition-all duration-200
                    hover:border-[#E8A900]
                    hover:bg-[#E8A900]
                    hover:text-[#111111]
                  "
                >
                  <social.icon className="h-[14px] w-[14px]" />
                </a>
              ))}
            </div>
          </div>

          {/* Columns */}
          {COLUMNS.map((column) => (
            <div key={column.title}>

              <h3
                className="
                  mb-3 sm:mb-5
                  text-[11px]
                  font-bold
                  uppercase
                  tracking-[0.13em]
                  text-[#171717]
                "
              >
                {column.title}
              </h3>

              <ul className="space-y-2 sm:space-y-3">

                {column.links.map((link) => (
                  <li key={link.label}>
                    <a
                      href={link.href}
                      className="
                        text-[12px]
                        font-medium
                        leading-5
                        text-[#737373]
                        transition-colors
                        duration-200
                        hover:text-[#E8A900]
                      "
                    >
                      {link.label}
                    </a>
                  </li>
                ))}

              </ul>

            </div>
          ))}

        </div>

        {/* Bottom */}
        <div className="border-t border-black/[0.08] py-5">

          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">

            {/* Contact information */}
            <div className="flex flex-wrap items-center gap-x-7 gap-y-2.5">

              <a
                href="tel:8962288774"
                className="
                  flex items-center gap-2
                  text-[11px]
                  font-medium
                  text-[#333333]
                  transition-colors
                  hover:text-[#E8A900]
                "
              >
                <Phone className="h-[14px] w-[14px] text-[#666666]" />
                89622 88774
              </a>

              <a
                href="mailto:contact.blussit@gmail.com"
                className="
                  flex items-center gap-2
                  text-[11px]
                  font-medium
                  text-[#333333]
                  transition-colors
                  hover:text-[#E8A900]
                "
              >
                <Mail className="h-[14px] w-[14px] text-[#666666]" />
                contact.blussit@gmail.com
              </a>

              <span className="flex items-center gap-2 text-[11px] font-medium text-[#333333]">
                <MapPin className="h-[14px] w-[14px] text-[#666666]" />
                Indore, MP
              </span>

            </div>

            {/* Copyright + build credit — the quietest line on the page, so
                it never competes with the contact details above it.
                rel="noopener noreferrer" because it opens in a new tab. */}
            <p className="text-[10px] font-medium text-[#999999]">
              © {new Date().getFullYear()} BLUSSIT. All rights reserved.
            </p>
            <p className="text-[10px] font-medium text-[#999999]">
              Developed by{" "}
              <a
                href="https://kalakartechcrew.com"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 transition-colors hover:text-[#333333]"
              >
                Kalakartechcrew
              </a>
            </p>

          </div>

        </div>

      </div>
    </footer>
  );
}