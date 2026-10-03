import { useState, useEffect, useRef } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Menu, X, ArrowRight, MapPin } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../context/AuthContext";
import { prefetchBooking } from "../../routes/prefetch";
import { roleHomePath } from "../../lib/roleHome";
import { ContactUsModal } from "../public/ContactUsModal";
import { type LandingSection, scrollToSection, sectionHref, sectionInView } from "../../lib/sections";

// One landing page: every link jumps to its section; Contact opens the form.
const navLinks: { label: string; section?: LandingSection }[] = [
  { label: "Home", section: "top" },
  { label: "Services", section: "services" },
  { label: "Plans", section: "plans" },
  { label: "Reviews", section: "reviews" },
  { label: "Contact Us" },
];

/** We serve Indore only — a plain label, not a picker. */
function LocationChip({ compact = false }: { compact?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-[#F3F6FA] font-semibold text-[#0E1A33] ${
        compact ? "px-2.5 py-1.5 text-[12px]" : "px-3.5 py-2 text-[13px]"
      }`}
    >
      <MapPin className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} strokeWidth={2.2} />
      Indore, MP
    </span>
  );
}

function BlussitLogo({ onClick }: { onClick?: (e: React.MouseEvent) => void }) {
  return (
    <Link
      to="/"
      onClick={onClick}
      className="flex flex-col items-start group cursor-pointer"
    >
      <img
        src="/img/blussit-logo-480.webp"
        alt="BLUSSIT"
        className="h-6 md:h-7 w-auto object-contain transition-transform duration-500 group-hover:-rotate-2 group-hover:scale-105"
      />

    </Link>
  );
}

export function PublicNavbar() {
  const [open, setOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const contactTriggerRef = useRef<HTMLButtonElement>(null);
  const headerRef = useRef<HTMLElement>(null);

  const { isAuthenticated, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const onLanding = location.pathname === "/";
  // Which section is on screen — drives the underline as the visitor scrolls.
  const [active, setActive] = useState<LandingSection | null>(onLanding ? "top" : null);
  useEffect(() => {
    if (!onLanding) {
      setActive(null);
      return;
    }
    const update = () => setActive(sectionInView());
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, [onLanding]);

  const goTo = (section: LandingSection) => (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // new tab etc.
    e.preventDefault();
    setOpen(false);
    if (onLanding && scrollToSection(section)) {
      window.history.replaceState(null, "", sectionHref(section));
      setActive(section);
    } else {
      navigate(sectionHref(section));
    }
  };
  const queryClient = useQueryClient();
  // Every public page has this bar: once the page is idle, quietly load the
  // booking wizard + its catalogue so tapping "Book Now" is instant.
  useEffect(() => prefetchBooking(queryClient), [queryClient]);
  // Quick-booking model: there is no sign-up — an account is created the
  // first time someone books, and login (OTP) is only to look things up.
  const authLabel = "Login";
  const authTarget = "/login";
  // A logged-in customer already has an account — send "Book Now" to the
  // after-login booking page instead of the guest-only wizard.
  const bookHref = user?.role === "customer" ? "/app/book" : "/book";

  // Mobile menu: tapping anywhere outside the header (or pressing Escape)
  // closes it, so it never sits open over the page.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (headerRef.current && !headerRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };

    window.addEventListener("scroll", handleScroll);

    return () => {
      window.removeEventListener("scroll", handleScroll);
    };
  }, []);

  return (
    <header
      ref={headerRef}
      id="top"
      className={`
        sticky top-0 z-50
        transition-all duration-500
        ${
          scrolled
            ? "bg-white/90 backdrop-blur-xl shadow-[0_8px_30px_rgba(15,30,60,0.08)] border-b border-[#E4E9F1] py-2.5 lg:py-3"
            : "bg-white/95 backdrop-blur-md border-b border-black/5 py-2.5 lg:py-3.5"
        }
      `}
    >
      <div className="container-page flex items-center justify-between gap-3">
        {/* Logo (mobile: menu button on its left, as in the v2 design) */}
        <div className="flex items-center gap-3">
          <button
            className="-ml-1 flex h-10 w-10 items-center justify-center rounded-xl text-[#0E1A33] transition-colors hover:bg-[#F3F6FA] xl:hidden"
            onClick={() => setOpen((o) => !o)}
            aria-label="Toggle menu"
            aria-expanded={open}
          >
            {open ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
          </button>
          <BlussitLogo onClick={goTo("top")} />
        </div>

        {/* Desktop Navigation */}
        <nav className="hidden items-center gap-8 whitespace-nowrap xl:flex">
          {navLinks.map((link) => !link.section ? (
            <button
              key={link.label}
              ref={contactTriggerRef}
              type="button"
              onClick={() => setContactOpen(true)}
              className="
                text-sm font-semibold text-[#071A3D]
                hover:text-[#1677FF]
                transition-colors
                relative
                cursor-pointer
                after:content-['']
                after:absolute
                after:-bottom-1.5
                after:left-0
                after:h-[2px]
                after:w-0
                after:bg-[#1677FF]
                after:rounded-full
                after:transition-all
                after:duration-300
                hover:after:w-full
                
              "
            >
              {link.label}
            </button>
          ) : (
            <a key={link.label} href={sectionHref(link.section)} onClick={goTo(link.section)} aria-current={active === link.section ? "location" : undefined} className={`
              text-sm font-semibold transition-colors relative hover:text-[#0A66F0]
              after:content-[''] after:absolute after:-bottom-2 after:left-0 after:h-[2px] after:bg-[#0A66F0] after:rounded-full after:transition-all after:duration-300 hover:after:w-full
              ${active === link.section ? "text-[#0E1A33] after:w-full" : "text-[#0E1A33] after:w-0"}
            `}>{link.label}</a>
          ))}
        </nav>

        {/* Desktop Actions */}
        <div className="hidden items-center gap-3 xl:flex">
          <LocationChip />
          {isAuthenticated ? (
            <button
              onClick={() => navigate(roleHomePath(user?.role))}
              className="
                group inline-flex cursor-pointer items-center gap-2
                rounded-[10px]
                bg-[#FBBF24] text-[#071A3D]
                px-6 py-2.5
                text-sm font-bold text-[#071A3D]
                transition-all duration-200
                hover:-translate-y-0.5
                hover:bg-[#FBBF24]
                
              "
            >
              Dashboard

              <ArrowRight
                className="
                  h-3.5 w-3.5
                  transition-transform
                  group-hover:translate-x-0.5
                "
              />
            </button>
          ) : (
            <>
              <button
                onClick={() => navigate(authTarget)}
                className="cursor-pointer rounded-xl bg-[#0A66F0] px-5 py-2.5 text-sm font-bold text-white shadow-[0_8px_18px_rgba(10,102,240,0.25)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#0858D0]"
              >
                {authLabel}
              </button>

              <a
                href={bookHref}
                className="group inline-flex cursor-pointer items-center gap-2 rounded-xl bg-[#FFD21F] px-5 py-2.5 text-sm font-bold text-[#0E1A33] shadow-[0_8px_18px_rgba(255,200,0,0.30)] transition-all duration-200 hover:-translate-y-0.5"
              >
                Book A Wash

                <ArrowRight
                  className="
                    h-3.5 w-3.5
                    transition-transform
                    group-hover:translate-x-0.5
                  "
                />
              </a>
            </>
          )}
        </div>

        {/* Mobile: location only — notifications live in the dashboards. */}
        <div className="flex items-center xl:hidden">
          <LocationChip compact />
        </div>
      </div>

      {/* Mobile Glass Menu */}
    <AnimatePresence>
  {open && (
    <motion.div
      initial={{ opacity: 0, y: -12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -12, scale: 0.98 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="
        absolute
        top-[calc(100%+10px)]
        left-4
        right-4
        z-50
        xl:hidden
        rounded-2xl
        border border-[#D9E8FF]/70
        bg-white
        backdrop-blur-xl
        shadow-[0_20px_50px_rgba(49,45,38,0.14)]
        p-3
      "
    >
      <nav className="flex flex-col items-center">

        {/* Navigation Links */}
        <div className="flex w-full flex-col items-center py-3">
          {navLinks.map((link) => !link.section ? (
            <button
              key={link.label}
              type="button"
              onClick={() => { setOpen(false); setContactOpen(true); }}
              className="
                flex
                w-full
                items-center
                justify-center
                rounded-xl
                px-5
                py-3
                text-base
                font-semibold
                text-[#071A3D]
                transition-all
                duration-200
                hover:bg-[#F5F9FF]
                hover:text-[#1677FF]
                cursor-pointer
              "
            >
              {link.label}
            </button>
          ) : (
            <a key={link.label} href={sectionHref(link.section)} onClick={goTo(link.section)} className={`
              flex w-full items-center justify-center rounded-xl px-5 py-3 text-base font-semibold transition-all duration-200 hover:bg-[#F5F9FF] hover:text-[#0A66F0]
              ${active === link.section ? "bg-[#F5F9FF] text-[#0A66F0]" : "text-[#071A3D]"}
            `}>{link.label}</a>
          ))}
        </div>

        {/* Divider */}
        <div className="my-2 h-px w-[90%] bg-[#E1D7C4]/70" />

        {/* Actions */}
        <div className="flex w-full flex-col items-center gap-3 px-1 pb-1 pt-2">
          {isAuthenticated ? (
            <button
              onClick={() => {
                setOpen(false);
                navigate(roleHomePath(user?.role));
              }}
              className="
                w-full
                rounded-xl
                bg-[#FFD21F] text-[#0E1A33]
                px-5
                py-3
                text-sm
                font-bold
                text-[#071A3D]
                transition-all
                duration-200
                hover:bg-[#FFD21F]
                
              "
            >
              Go To Dashboard
            </button>
          ) : (
            <>
              <button
                onClick={() => {
                  setOpen(false);
                  navigate(authTarget);
                }}
                className="w-full rounded-xl bg-[#0A66F0] px-5 py-3 text-sm font-bold text-white shadow-[0_8px_18px_rgba(10,102,240,0.25)] transition-all duration-200 hover:-translate-y-0.5 hover:bg-[#0858D0]"
              >
                {authLabel}
              </button>

              <a
                href={bookHref}
                onClick={() => setOpen(false)}
                className="
                  flex
                  w-full
                  items-center
                  justify-center
                  gap-2
                  rounded-xl
                  bg-[#FFD21F] text-[#0E1A33]
                  px-5
                  py-3
                  text-sm
                  font-bold
                  text-[#071A3D]
                  transition-all
                  duration-200
                  hover:-translate-y-0.5
                  hover:bg-[#FFD21F]
                  
                "
              >
                Book A Wash
                <ArrowRight className="h-4 w-4" />
              </a>
            </>
          )}
        </div>
      </nav>
    </motion.div>
  )}
</AnimatePresence>
      <ContactUsModal open={contactOpen} onClose={() => setContactOpen(false)} returnFocusRef={contactTriggerRef} />
    </header>
  );
}
