import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { GoogleSignInButton } from "../../components/shared/GoogleSignInButton";

export default function RegisterPage() {
  const { register } = useAuth();
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", password: "" });
  const navigate = useNavigate();
  const shouldReduceMotion = useReducedMotion();

  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.email && !form.phone) {
      setError("Please provide an email or phone number");
      return;
    }
    setIsLoading(true);
    try {
      await register({
        full_name: form.full_name,
        email: form.email || undefined,
        phone: form.phone || undefined,
        password: form.password,
      });
      navigate("/app");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <motion.main
      initial={{ opacity: 0, y: shouldReduceMotion ? 0 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="flex min-h-dvh flex-col bg-[#FDF9EE] text-[#111111]"
    >
      <PublicNavbar />

      {/* Photo-free, centered form — same card at every width. */}
      <section className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-[420px] rounded-[20px] border border-[#ECE7D8] bg-white px-6 py-8 shadow-[0_16px_46px_rgba(39,33,20,0.08)] sm:px-8 sm:py-9">
          <Link to="/" aria-label="Blussit home" className="mb-5 flex w-full flex-col items-center">
            <img src="/blussit-logo.png" alt="BLUSSIT" className="h-auto w-[142px] object-contain" />
            <span className="mt-0.5 whitespace-nowrap text-[7px] font-bold uppercase tracking-[0.12em] text-[#E8A900]">
              Premium Car Wash At Doorstep.
            </span>
          </Link>

          <div className="mb-5 text-center">
            <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">Create your account</h1>
            <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">Book your first doorstep service in minutes.</p>
          </div>

          <form onSubmit={handleSubmit}>
            <label className="block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Full name</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <PersonIcon />
                </div>
                <input
                  type="text"
                  value={form.full_name}
                  onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                  required
                  placeholder="Enter your full name"
                  className="h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                />
              </div>
            </label>

            <label className="mt-2.5 block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Email</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <MailIcon />
                </div>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  placeholder="you@gmail.com"
                  className="h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                />
              </div>
              {/* Stated up front — the server enforces the same list
                  (backend/app/utils/email_domains.py). */}
              <span className="mt-1 block text-[11px] text-[#8A8A8A]">Gmail, Yahoo, Outlook/Hotmail, iCloud, Rediffmail, Proton or Zoho only.</span>
            </label>

            <label className="mt-2.5 block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Phone number</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <PhoneIcon />
                </div>
                <input
                  type="text"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                  placeholder="Enter your phone number"
                  className="h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                />
              </div>
            </label>

            <label className="mt-2.5 block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Password</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <LockIcon />
                </div>
                <input
                  type={showPassword ? "text" : "password"}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  required
                  placeholder="At least 8 characters"
                  className="h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-[48px] text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-[#222] transition-opacity hover:opacity-60"
                >
                  {showPassword ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
              {form.password.length > 0 && form.password.length < 8 && (
                <span className="mt-1 block text-[11px] font-medium text-[var(--color-error)]">Enter at least 8 characters.</span>
              )}
            </label>

            {error && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">{error}</div>
            )}

            <button
              type="submit"
              disabled={isLoading || form.password.length < 8}
              className="group relative mt-4 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#F5B400] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(245,180,0,0.18)] transition-all hover:bg-[#EAAA00] hover:shadow-[0_10px_24px_rgba(245,180,0,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span>{isLoading ? "Creating account..." : "Create Account"}</span>
              {!isLoading && (
                <span className="absolute right-5 text-[18px] font-normal transition-transform duration-200 group-hover:translate-x-1">→</span>
              )}
            </button>
          </form>

          <div className="mt-3">
            <GoogleSignInButton />
          </div>

          <p className="mt-4 text-center text-[12px] text-[#737B88]">
            Already have an account?{" "}
            <Link to="/login" className="font-semibold text-[#D99700] hover:text-[#B87900]">
              Log in
            </Link>
          </p>
        </div>
      </section>
    </motion.main>
  );
}

/* =========================================================
   ICONS
========================================================= */

function PersonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="8" r="3.6" stroke="#111" strokeWidth="1.8" />
      <path d="M4.5 20C5.5 15.8 8.4 13.5 12 13.5C15.6 13.5 18.5 15.8 19.5 20" stroke="#111" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="5" y="10" width="14" height="11" rx="2" stroke="#111" strokeWidth="1.8" />
      <path d="M8 10V7C8 4.8 9.8 3 12 3C14.2 3 16 4.8 16 7V10" stroke="#111" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M2.5 12C4.5 7.8 7.8 5.5 12 5.5C16.2 5.5 19.5 7.8 21.5 12C19.5 16.2 16.2 18.5 12 18.5C7.8 18.5 4.5 16.2 2.5 12Z" stroke="#111" strokeWidth="1.8" />
      <circle cx="12" cy="12" r="3" stroke="#111" strokeWidth="1.8" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3 3L21 21" stroke="#111" strokeWidth="1.8" strokeLinecap="round" />
      <path
        d="M10.6 5.8C11.05 5.7 11.52 5.65 12 5.65C16.2 5.65 19.5 7.9 21.5 12C20.7 13.65 19.65 15.05 18.4 16.1"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <path
        d="M6.25 7.3C4.65 8.4 3.4 9.95 2.5 12C4.5 16.1 7.8 18.35 12 18.35C13.25 18.35 14.4 18.15 15.45 17.75"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 8L10.89 13.26C11.56 13.7 12.44 13.7 13.11 13.26L21 8M5 19H19C20.1 19 21 18.1 21 17V7C21 5.9 20.1 5 19 5H5C3.9 5 3 5.9 3 7V17C3 18.1 3.9 19 5 19Z"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PhoneIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M22 16.92V19.92C22 20.45 21.56 20.91 21.03 20.92C18.11 20.98 15.32 20.08 12.91 18.42C10.66 16.89 8.79 15.02 7.26 12.77C5.6 10.36 4.71 7.57 4.76 4.65C4.77 4.12 5.21 3.68 5.74 3.68H8.74C9.22 3.68 9.63 4.04 9.71 4.51C9.83 5.37 10.05 6.2 10.36 7C10.48 7.31 10.4 7.66 10.17 7.89L8.44 9.62C9.86 12.11 11.89 14.14 14.38 15.56L16.11 13.83C16.34 13.6 16.69 13.52 17 13.64C17.8 13.95 18.63 14.17 19.49 14.29C19.96 14.37 20.32 14.78 20.32 15.26V16.92H22Z"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
