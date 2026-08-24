import { useEffect, useState } from "react";
import { Menu, X, ArrowRight } from "lucide-react";
import { siteConfig } from "@/config/site";
import { track } from "@/services/analytics";

const links = [{ label: "Why BLUSSIT", href: "#why" }];

export function Navigation() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const scrollToForm = () => {
    track("early_access_click", { location: "nav" });
    document.getElementById("early-access")?.scrollIntoView({ behavior: "smooth" });
    setOpen(false);
  };

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-all duration-300 ${
        scrolled
          ? "bg-cream/90 backdrop-blur-md shadow-[0_1px_0_0_rgba(16,17,20,0.06)]"
          : "bg-transparent"
      }`}
    >
      <nav className="mx-auto flex max-w-content items-center justify-between px-6 py-4 md:px-10">
        <a href="#top" className="flex items-center" aria-label={siteConfig.brandName}>
          <img src="/blussit-logo.png" alt={siteConfig.brandName} className="h-6 w-auto md:h-7" />
        </a>

        <div className="hidden items-center gap-8 md:flex">
          {links.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-sm font-semibold text-ink/70 transition-colors hover:text-goldDeep"
            >
              {link.label}
            </a>
          ))}
          <button
            onClick={scrollToForm}
            className="inline-flex items-center gap-1.5 rounded-xl bg-gold px-5 py-2.5 text-sm font-bold text-ink transition-colors hover:bg-gold/90"
          >
            Notify Me <ArrowRight size={15} />
          </button>
        </div>

        <button
          className="md:hidden"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X size={24} /> : <Menu size={24} />}
        </button>
      </nav>

      {open && (
        <div className="border-t border-border bg-cream px-6 pb-6 pt-2 md:hidden">
          <div className="flex flex-col gap-4">
            {links.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="text-base font-semibold text-ink/80"
              >
                {link.label}
              </a>
            ))}
            <button
              onClick={scrollToForm}
              className="mt-2 rounded-xl bg-gold px-5 py-3 text-center text-sm font-bold text-ink"
            >
              Notify Me
            </button>
          </div>
        </div>
      )}
    </header>
  );
}
