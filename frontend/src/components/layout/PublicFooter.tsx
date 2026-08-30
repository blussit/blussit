import { Mail, MapPin, Phone, MessageCircle } from "lucide-react";

function Instagram({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}
function Facebook({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M13.5 21v-7.5H16l.5-3.5h-3V7.7c0-1 .3-1.7 1.7-1.7H16.5V2.8C16.2 2.8 15.2 2.7 14 2.7c-2.4 0-4 1.5-4 4.2V10H7.5v3.5H10V21h3.5Z" />
    </svg>
  );
}
function Linkedin({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M6.5 8.5H3.8V21h2.7V8.5ZM5.1 7.3a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2ZM21 13.9c0-3.1-1.7-4.6-4-4.6-1.8 0-2.6 1-3.1 1.7V8.5h-2.7V21h2.7v-6.6c0-1.7.8-2.7 2.2-2.7 1.3 0 2.1.9 2.1 2.7V21H21v-7.1Z" />
    </svg>
  );
}

const COLUMNS: { title: string; links: { label: string; href: string }[] }[] = [
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
    <footer className="bg-white text-black pt-24 pb-8 border-t border-black/5 relative overflow-hidden">
      
      <div className="container-page relative z-10 grid grid-cols-1 gap-12 sm:grid-cols-2 lg:grid-cols-6 mb-16">
        <div className="lg:col-span-2">
          <div className="flex items-center gap-3">
            <img src="/blussit-mark.png" alt="Blussit" className="h-10 w-auto object-contain" />
            <img src="/blussit-logo.png" alt="Blussit" className="h-7 w-auto object-contain" />
          </div>
          <p className="mt-6 text-[11px] font-bold uppercase tracking-[0.2em] text-neutral-500">Premium Doorstep Vehicle Care</p>
          <p className="mt-2 text-sm text-neutral-500 leading-relaxed max-w-xs">Your car. Our care. Anywhere. Experience the ultimate convenience in vehicle maintenance.</p>
          <div className="mt-8 flex gap-4">
            {[Instagram, Facebook, MessageCircle, Linkedin].map((Icon, i) => (
              <span key={i} className="flex h-10 w-10 items-center justify-center rounded-full bg-black/5 border border-black/10 text-black/70 transition-all hover:border-yellow-400 hover:text-black hover:bg-yellow-400 hover:-translate-y-1 cursor-pointer">
                <Icon className="h-4 w-4" />
              </span>
            ))}
          </div>
        </div>

        {COLUMNS.map((col) => (
          <div key={col.title}>
            <p className="text-sm font-bold tracking-wider uppercase text-black mb-6">{col.title}</p>
            <ul className="space-y-4">
              {col.links.map((l) => (
                <li key={l.label}>
                  <a href={l.href} className="text-sm text-neutral-500 font-medium transition-colors hover:text-yellow-600">{l.label}</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      
      <div className="container-page relative z-10">
        <div className="border-t border-black/10 py-8 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex flex-wrap items-center justify-center md:justify-start gap-6 text-sm text-black font-medium">
            <span className="flex items-center gap-2"><Phone className="h-4 w-4" /> 89622 88774</span>
            <span className="flex items-center gap-2"><Mail className="h-4 w-4" /> contact.blussit@gmail.com</span>
            <span className="flex items-center gap-2"><MapPin className="h-4 w-4" /> Indore, MP</span>
          </div>
          <p className="text-sm text-neutral-400 font-medium">© 2026 BLUSSIT. All rights reserved.</p>
        </div>
      </div>
    </footer>
  );
}
