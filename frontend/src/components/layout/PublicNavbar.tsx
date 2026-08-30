import { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Menu, X } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { roleHomePath } from "../../lib/roleHome";

const navLinks = [
  { label: "Services", href: "/services" },
  { label: "Plans", href: "/plans" },
  { label: "Why BLUSSIT", href: "/#why" },
  { label: "How It Works", href: "/#how-it-works" },
];

function BlussitLogo() {
  return (
    <Link to="/" className="flex items-center gap-3 group">
      <img 
        src="/blussit-mark.png" 
        alt="Blussit Mark" 
        className="h-8 w-auto object-contain transition-transform duration-500 group-hover:rotate-12"
      />
      <img 
        src="/blussit-logo.png" 
        alt="Blussit Logo" 
        className="h-6 w-auto object-contain hidden sm:block" 
      />
    </Link>
  );
}

export function PublicNavbar() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const { isAuthenticated, user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header 
      id="top" 
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-500 ${
        scrolled 
          ? "bg-white/90 backdrop-blur-md border-b border-black/5 shadow-sm py-3" 
          : "bg-transparent border-b border-transparent py-5"
      }`}
    >
      <div className="container-page flex items-center justify-between">
        <BlussitLogo />

        <nav className="hidden items-center gap-8 lg:flex">
          {navLinks.map((link) => (
            <a 
              key={link.label} 
              href={link.href} 
              className="text-sm font-bold text-black/70 transition-all hover:text-black relative after:content-[''] after:absolute after:-bottom-1 after:left-0 after:h-[2px] after:w-0 after:bg-black after:transition-all hover:after:w-full"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-6 lg:flex">
          {isAuthenticated ? (
            <button
              onClick={() => navigate(roleHomePath(user?.role))}
              className="rounded-full px-6 py-2.5 text-sm font-bold text-yellow-400 bg-black hover:bg-yellow-400 hover:text-black transition-all hover:-translate-y-0.5 shadow-md"
            >
              Dashboard
            </button>
          ) : (
            <>
              <button onClick={() => navigate("/login")} className="text-sm font-bold text-black/70 hover:text-yellow-500 transition-colors">
                Login
              </button>
              <a 
                href="/book" 
                className="rounded-full px-6 py-2.5 text-sm font-bold text-yellow-400 bg-black hover:bg-yellow-400 hover:text-black transition-all hover:-translate-y-0.5 shadow-md"
              >
                Book a Service
              </a>
            </>
          )}
        </div>

        <button className="p-2 text-black lg:hidden" onClick={() => setOpen((o) => !o)} aria-label="Toggle menu">
          {open ? <X className="h-6 w-6" /> : <Menu className="h-6 w-6" />}
        </button>
      </div>

      {open && (
        <div className="absolute top-full left-0 right-0 bg-white/95 backdrop-blur-xl border-b border-black/10 px-5 py-6 lg:hidden shadow-2xl">
          <nav className="flex flex-col gap-4">
            {navLinks.map((link) => (
              <a key={link.label} href={link.href} className="text-lg font-bold text-black/90" onClick={() => setOpen(false)}>
                {link.label}
              </a>
            ))}
            <div className="mt-4 flex flex-col gap-3 pt-4 border-t border-black/10">
              {isAuthenticated ? (
                <button onClick={() => navigate(roleHomePath(user?.role))} className="rounded-full px-5 py-3 text-sm font-bold text-yellow-400 bg-black hover:bg-yellow-400 hover:text-black transition-colors">
                  Go to dashboard
                </button>
              ) : (
                <>
                  <button onClick={() => navigate("/login")} className="rounded-full border border-black/20 px-5 py-3 text-sm font-bold text-black hover:bg-black/5">
                    Login
                  </button>
                  <a
                    href="/book"
                    onClick={() => setOpen(false)}
                    className="rounded-full px-5 py-3 text-center text-sm font-bold text-yellow-400 bg-black hover:bg-yellow-400 hover:text-black transition-colors"
                  >
                    Book a Service
                  </a>
                </>
              )}
            </div>
          </nav>
        </div>
      )}
    </header>
  );
}
