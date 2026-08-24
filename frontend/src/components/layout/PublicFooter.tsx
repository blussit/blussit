import { Link } from "react-router-dom";
import { Mail, MapPin, MessageCircle, Phone, Sparkles } from "lucide-react";

// lucide-react no longer ships brand/logo icons (Facebook, Instagram, etc.),
// so these are kept as small inline SVGs.
function Facebook({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M13.5 21v-7.5H16l.5-3.5h-3V7.7c0-1 .3-1.7 1.7-1.7H16.5V2.8C16.2 2.8 15.2 2.7 14 2.7c-2.4 0-4 1.5-4 4.2V10H7.5v3.5H10V21h3.5Z" />
    </svg>
  );
}

function Instagram({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className} aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function PublicFooter() {
  return (
    <footer className="bg-[var(--color-primary)] text-white">
      <div className="container-page grid grid-cols-1 gap-10 py-14 md:grid-cols-4">
        <div>
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-secondary)] text-[var(--color-primary)]">
              <Sparkles className="h-5 w-5" />
            </span>
            <span className="font-display text-lg font-bold tracking-tight">CLEANRIDE</span>
          </Link>
          <p className="mt-4 max-w-xs text-sm text-white/60">
            Professional car wash, delivered to your doorstep. Verified captains, transparent pricing, satisfaction guaranteed.
          </p>
        </div>

        <div>
          <h4 className="mb-4 text-sm font-semibold">Quick Links</h4>
          <ul className="space-y-2.5 text-sm text-white/60">
            <li><a href="/#top" className="hover:text-[var(--color-secondary)]">Home</a></li>
            <li><a href="/#services" className="hover:text-[var(--color-secondary)]">Services</a></li>
            <li><a href="/#plans" className="hover:text-[var(--color-secondary)]">Pricing</a></li>
            <li><a href="/#how-it-works" className="hover:text-[var(--color-secondary)]">How It Works</a></li>
          </ul>
        </div>

        <div>
          <h4 className="mb-4 text-sm font-semibold">Company</h4>
          <ul className="space-y-2.5 text-sm text-white/60">
            <li><a href="/#about" className="hover:text-[var(--color-secondary)]">About Us</a></li>
            <li><Link to="/login" className="hover:text-[var(--color-secondary)]">Captain login</Link></li>
            <li><Link to="/login" className="hover:text-[var(--color-secondary)]">Manager login</Link></li>
            <li><Link to="/login" className="hover:text-[var(--color-secondary)]">Admin login</Link></li>
          </ul>
        </div>

        <div>
          <h4 className="mb-4 text-sm font-semibold">Follow Us</h4>
          <div className="flex gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10">
              <Facebook className="h-4 w-4" />
            </span>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10">
              <Instagram className="h-4 w-4" />
            </span>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10">
              <MessageCircle className="h-4 w-4" />
            </span>
          </div>
          <ul className="mt-5 space-y-2.5 text-sm text-white/60">
            <li className="flex items-center gap-2"><Phone className="h-4 w-4 text-[var(--color-secondary)]" /> +91 12345 67890</li>
            <li className="flex items-center gap-2"><Mail className="h-4 w-4 text-[var(--color-secondary)]" /> hello@cleanride.in</li>
            <li className="flex items-center gap-2"><MapPin className="h-4 w-4 text-[var(--color-secondary)]" /> Indore, Madhya Pradesh</li>
          </ul>
        </div>
      </div>
      <div className="border-t border-white/10 py-5 text-center text-xs text-white/50">
        © {new Date().getFullYear()} CleanRide. All rights reserved.
      </div>
    </footer>
  );
}
