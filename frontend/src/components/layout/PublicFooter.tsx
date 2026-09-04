import {
  Mail,
  MapPin,
  Phone,
  MessageCircle,
} from "lucide-react";

function Instagram({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      className={className}
      aria-hidden="true"
    >
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle
        cx="17.5"
        cy="6.5"
        r="1"
        fill="currentColor"
        stroke="none"
      />
    </svg>
  );
}

function Facebook({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M13.5 21v-7.5H16l.5-3.5h-3V7.7c0-1 .3-1.7 1.7-1.7H16.5V2.8C16.2 2.8 15.2 2.7 14 2.7c-2.4 0-4 1.5-4 4.2V10H7.5v3.5H10V21h3.5Z" />
    </svg>
  );
}

function Linkedin({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M6.5 8.5H3.8V21h2.7V8.5ZM5.1 7.3a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2ZM21 13.9c0-3.1-1.7-4.6-4-4.6-1.8 0-2.6 1-3.1 1.7V8.5h-2.7V21h2.7v-6.6c0-1.7.8-2.7 2.2-2.7 1.3 0 2.1.9 2.1 2.7V21H21v-7.1Z" />
    </svg>
  );
}

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
      { label: "About Us", href: "/#why" },
      { label: "How It Works", href: "/#how-it-works" },
      { label: "Careers", href: "#" },
      { label: "Contact Us", href: "#" },
    ],
  },
  {
    title: "Support",
    links: [
      { label: "FAQ", href: "#" },
      { label: "Cancellation Policy", href: "#" },
      { label: "Terms & Conditions", href: "#" },
      { label: "Privacy Policy", href: "#" },
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
                src="/blussit-mark.png"
                alt=""
                className="h-9 w-auto object-contain"
              />

              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="ml-2 h-[23px] w-auto object-contain"
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

              {[Instagram, Facebook, MessageCircle, Linkedin].map(
                (Icon, i) => (
                  <a
                    key={i}
                    href="#"
                    aria-label="Social media"
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
                    <Icon className="h-[14px] w-[14px]" />
                  </a>
                )
              )}

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