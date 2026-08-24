import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Menu, X, Sparkles } from "lucide-react";
import { Button } from "../ui";
import { useAuth } from "../../context/AuthContext";
import { roleHomePath } from "../../lib/roleHome";

const navLinks = [
  { label: "Home", href: "/#top" },
  { label: "About Us", href: "/#about" },
  { label: "Services", href: "/#services" },
  { label: "How It Works", href: "/#how-it-works" },
  { label: "Pricing", href: "/#plans" },
  { label: "Contact Us", href: "/#contact" },
];

export function PublicNavbar() {
  const [open, setOpen] = useState(false);
  const { isAuthenticated, user } = useAuth();
  const navigate = useNavigate();

  return (
    <header id="top" className="sticky top-0 z-40 border-b border-gray-100 bg-white/95 backdrop-blur-md">
      <div className="container-page flex h-16 items-center justify-between">
        <Link to="/" className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-primary)] text-[var(--color-secondary)]">
            <Sparkles className="h-5 w-5" />
          </span>
          <span className="leading-tight">
            <span className="block font-display text-lg font-bold tracking-tight text-[var(--color-primary)]">CLEANRIDE</span>
            <span className="block text-[9px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-secondary)]">
              Car wash at your doorstep
            </span>
          </span>
        </Link>

        <nav className="hidden items-center gap-7 lg:flex">
          {navLinks.map((link) => (
            <a key={link.label} href={link.href} className="text-sm font-medium text-gray-600 transition-colors hover:text-[var(--color-primary)]">
              {link.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-3 lg:flex">
          {isAuthenticated ? (
            <Button onClick={() => navigate(roleHomePath(user?.role))}>Go to dashboard</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => navigate("/login")}>
                Log in
              </Button>
              <Button onClick={() => navigate("/register")}>Book Now</Button>
            </>
          )}
        </div>

        <button className="p-2 lg:hidden" onClick={() => setOpen((o) => !o)} aria-label="Toggle menu">
          {open ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </div>

      {open && (
        <div className="border-t border-gray-100 bg-white px-5 py-4 lg:hidden">
          <nav className="flex flex-col gap-3">
            {navLinks.map((link) => (
              <a key={link.label} href={link.href} className="text-sm font-medium text-gray-700" onClick={() => setOpen(false)}>
                {link.label}
              </a>
            ))}
            <div className="mt-2 flex flex-col gap-2">
              {isAuthenticated ? (
                <Button onClick={() => navigate(roleHomePath(user?.role))}>Go to dashboard</Button>
              ) : (
                <>
                  <Button variant="outline" onClick={() => navigate("/login")}>
                    Log in
                  </Button>
                  <Button onClick={() => navigate("/register")}>Book Now</Button>
                </>
              )}
            </div>
          </nav>
        </div>
      )}
    </header>
  );
}
