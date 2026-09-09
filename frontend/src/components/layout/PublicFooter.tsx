import {
  Mail,
  MapPin,
  Phone,
  MessageCircle,
} from "lucide-react";

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
      { label: "Monthly Plans", href: "/plans" },
      { label: "Yearly Plans", href: "/plans" },
      { label: "Corporate Plans", href: "/plans" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "About Us", href: "/#how-it-works" },
      { label: "How It Works", href: "/#how-it-works" },
      { label: "Cancellation Policy", href: "/cancellation-policy" },
    ],
  },
  {
    title: "Support",
    links: [
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

        {/* Main footer */}
        <div className="grid grid-cols-1 gap-10 py-12 sm:grid-cols-2 lg:grid-cols-[1.8fr_1fr_1fr_1fr_1fr] lg:gap-8">

          {/* Brand */}
          <div className="lg:pr-12">

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

            {/* Social icons */}
            <div className="mt-5 flex items-center gap-2.5">

              {/* Only channels that actually exist — a dead social icon is
                  worse than none. Add Instagram/Facebook/LinkedIn back here
                  with real profile URLs when those accounts go live. */}
              <a
                href={`https://wa.me/${import.meta.env.VITE_WHATSAPP_NUMBER || "918962288774"}`}
                target="_blank"
                rel="noreferrer"
                aria-label="Chat with us on WhatsApp"
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
                <MessageCircle className="h-[14px] w-[14px]" />
              </a>

            </div>
          </div>

          {/* Columns */}
          {COLUMNS.map((column) => (
            <div key={column.title}>

              <h3
                className="
                  mb-5
                  text-[11px]
                  font-bold
                  uppercase
                  tracking-[0.13em]
                  text-[#171717]
                "
              >
                {column.title}
              </h3>

              <ul className="space-y-3">

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

            {/* Copyright */}
            <p className="text-[10px] font-medium text-[#999999]">
              © {new Date().getFullYear()} BLUSSIT. All rights reserved.
            </p>

          </div>

        </div>

      </div>
    </footer>
  );
}