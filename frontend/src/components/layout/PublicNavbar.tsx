import { useState, useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Menu, X, ArrowRight } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { useAuth } from "../../context/AuthContext";
import { roleHomePath } from "../../lib/roleHome";
import { ContactUsModal } from "../public/ContactUsModal";

const navLinks = [
  { label: "Home", href: "/" },
  { label: "Services", href: "/services" },
  { label: "How It Works", href: "/#how-it-works" },
  { label: "Plans", href: "/plans" },
  { label: "Contact Us", href: "#contact" },
];

function BlussitLogo() {
  return (
    <Link
      to="/"
      className="flex flex-col items-start group cursor-default"
    >
      <img
        src="/blussit-logo.png"
        alt="BLUSSIT"
        className="h-6 md:h-7 w-auto object-contain transition-transform duration-500 group-hover:-rotate-2 group-hover:scale-105"
      />

      <span
        className="mt-2 text-[9px] font-medium tracking-[3.5px] text-[#E8A900] leading-none"
        style={{
          fontFamily: "'Montserrat', sans-serif",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
        }}
      >
        CLEAN CAR. CLEAR MIND.
      </span>
    </Link>
  );
}

export function PublicNavbar() {
  const [open, setOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const contactTriggerRef = useRef<HTMLButtonElement>(null);

  const { isAuthenticated, user } = useAuth();
  const navigate = useNavigate();

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
      id="top"
      className={`
        sticky top-0 z-50
        transition-all duration-500
        ${
          scrolled
            ? "bg-white/85 backdrop-blur-xl shadow-[0_8px_30px_rgba(49,45,38,0.08)] border-b border-[#E1D7C4]/70 py-3"
            : "bg-white/95 backdrop-blur-md border-b border-black/5 py-5"
        }
      `}
    >
      <div className="container-page flex items-center justify-between">
        {/* Logo */}
        <BlussitLogo />

        {/* Desktop Navigation */}
        <nav className="hidden items-center gap-9 lg:flex">
          {navLinks.map((link) => link.label === "Contact Us" ? (
            <button
              key={link.label}
              ref={contactTriggerRef}
              type="button"
              onClick={() => setContactOpen(true)}
              className="
                text-sm font-semibold text-black/70
                hover:text-[#E8A900]
                transition-colors
                relative
                after:content-['']
                after:absolute
                after:-bottom-1.5
                after:left-0
                after:h-[2px]
                after:w-0
                after:bg-[#E8A900]
                after:rounded-full
                after:transition-all
                after:duration-300
                hover:after:w-full
              "
            >
              {link.label}
            </button>
          ) : (
            <a key={link.label} href={link.href} className="
              text-sm font-semibold text-black/70 hover:text-[#E8A900] transition-colors relative
              after:content-[''] after:absolute after:-bottom-1.5 after:left-0 after:h-[2px] after:w-0 after:bg-[#E8A900] after:rounded-full after:transition-all after:duration-300 hover:after:w-full
            ">{link.label}</a>
          ))}
        </nav>

        {/* Desktop Actions */}
        <div className="hidden items-center gap-3 lg:flex">
          {isAuthenticated ? (
            <button
              onClick={() => navigate(roleHomePath(user?.role))}
              className="
                group inline-flex cursor-pointer items-center gap-2
                rounded-[10px]
                bg-[#E8A900]
                px-6 py-2.5
                text-sm font-bold text-white
                shadow-[0_6px_16px_rgba(232,169,0,0.24)]
                transition-all duration-200
                hover:-translate-y-0.5
                hover:bg-[#D99A00]
                hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)]
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
                onClick={() => navigate("/login")}
                className="
                  cursor-pointer
                  rounded-[10px]
                  border border-black/15
                  px-5 py-2.5
                  text-sm font-semibold
                  text-black/80
                  transition-all duration-200
                  hover:border-[#E8A900]/50
                  hover:text-[#A87400]
                  hover:bg-[#FFF4CD]/30
                "
              >
                Login
              </button>

              <a
                href="/book"
                className="
                  group inline-flex cursor-pointer items-center gap-2
                  rounded-[10px]
                  bg-[#E8A900]
                  px-6 py-2.5
                  text-sm font-bold text-white
                  shadow-[0_6px_16px_rgba(232,169,0,0.24)]
                  transition-all duration-200
                  hover:-translate-y-0.5
                  hover:bg-[#D99A00]
                  hover:shadow-[0_10px_22px_rgba(232,169,0,0.30)]
                "
              >
                Book Now

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

        {/* Mobile Menu Button */}
        <button
          className="
            lg:hidden
            flex items-center justify-center
            h-10 w-10
            rounded-xl
            border border-[#E1D7C4]/80
            bg-white/60
            backdrop-blur-lg
            text-[#312D26]
            shadow-[0_4px_16px_rgba(49,45,38,0.06)]
            transition-all duration-200
            hover:bg-[#FFF4CD]/60
            hover:border-[#E8A900]/40
          "
          onClick={() => setOpen((o) => !o)}
          aria-label="Toggle menu"
          aria-expanded={open}
        >
          {open ? (
            <X className="h-5 w-5" />
          ) : (
            <Menu className="h-5 w-5" />
          )}
        </button>
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
        lg:hidden
        rounded-2xl
        border border-[#E1D7C4]/70
        bg-[#FFFCF5]/90
        backdrop-blur-xl
        shadow-[0_20px_50px_rgba(49,45,38,0.14)]
        p-3
      "
    >
      <nav className="flex flex-col items-center">

        {/* Navigation Links */}
        <div className="flex w-full flex-col items-center py-3">
          {navLinks.map((link) => link.label === "Contact Us" ? (
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
                text-[#312D26]
                transition-all
                duration-200
                hover:bg-[#FFF4CD]
                hover:text-[#A87400]
              "
            >
              {link.label}
            </button>
          ) : (
            <a key={link.label} href={link.href} onClick={() => setOpen(false)} className="
              flex w-full items-center justify-center rounded-xl px-5 py-3 text-base font-semibold text-[#312D26] transition-all duration-200 hover:bg-[#FFF4CD] hover:text-[#A87400]
            ">{link.label}</a>
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
                bg-[#E8A900]
                px-5
                py-3
                text-sm
                font-bold
                text-white
                shadow-[0_6px_18px_rgba(232,169,0,0.22)]
                transition-all
                duration-200
                hover:bg-[#D99A00]
              "
            >
              Go to Dashboard
            </button>
          ) : (
            <>
              <button
                onClick={() => {
                  setOpen(false);
                  navigate("/login");
                }}
                className="
                  w-full
                  rounded-xl
                  border
                  border-[#E1D7C4]
                  bg-white/70
                  px-5
                  py-3
                  text-sm
                  font-bold
                  text-[#312D26]
                  transition-all
                  duration-200
                  hover:border-[#E8A900]
                  hover:bg-[#FFF4CD]/60
                "
              >
                Login
              </button>

              <a
                href="/book"
                onClick={() => setOpen(false)}
                className="
                  flex
                  w-full
                  items-center
                  justify-center
                  gap-2
                  rounded-xl
                  bg-[#E8A900]
                  px-5
                  py-3
                  text-sm
                  font-bold
                  text-white
                  shadow-[0_7px_20px_rgba(232,169,0,0.24)]
                  transition-all
                  duration-200
                  hover:-translate-y-0.5
                  hover:bg-[#D99A00]
                "
              >
                Book Now
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
