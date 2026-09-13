import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { roleHomePath } from "../../lib/roleHome";
import { GoogleSignInButton } from "../../components/shared/GoogleSignInButton";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const shouldReduceMotion = useReducedMotion();

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);

    try {
      const user = await login(identifier, password);
      // Honor the deep-link ProtectedRoute captured (state.from) — but only
      // for the customer portal; staff always land on their own home.
      const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
      navigate(from && user.role === "customer" ? from : roleHomePath(user.role), { replace: true });
    } catch (err) {
      // Keep it short and unambiguous: the two things a customer can fix
      // are the id and the password. Anything else (locked out, rate
      // limited) keeps the server's own wording, which explains itself.
      const message = getErrorMessage(err);
      setError(/invalid credentials/i.test(message) ? "Incorrect email/phone or password" : message);
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
          <Link to="/" aria-label="Blussit home" className="mb-6 flex w-full flex-col items-center">
            <img src="/blussit-logo.png" alt="BLUSSIT" className="h-auto w-[142px] object-contain" />
            <span className="mt-0.5 whitespace-nowrap text-[7px] font-bold uppercase tracking-[0.12em] text-[#E8A900]">
              Premium Car Wash At Doorstep.
            </span>
          </Link>

          <div className="mb-6 text-center">
            <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">Welcome Back!</h1>
            <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">Login to continue to your dashboard.</p>
          </div>

          <form onSubmit={handleSubmit}>
            <label className="block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Email or phone</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <PersonIcon />
                </div>
                <input
                  type="text"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  required
                  autoComplete="username"
                  placeholder="Enter email or phone number"
                  className="h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                />
              </div>
            </label>

            <label className="mt-3 block">
              <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Password</span>
              <div className="relative">
                <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                  <LockIcon />
                </div>
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="current-password"
                  placeholder="Enter your password"
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
            </label>

            <div className="mt-3 flex items-center justify-between">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="h-4 w-4 cursor-pointer rounded border-[#D9DDE3] text-[#E9AA00] focus:ring-[#F5B400]/30"
                />
                <span className="text-[12.5px] font-medium text-[#4A5057]">Remember me</span>
              </label>
              <Link to="/forgot-password" className="text-[12.5px] font-semibold text-[#D99400] transition-colors hover:text-[#B87800]">
                Forgot Password?
              </Link>
            </div>

            {error && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">{error}</div>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="group relative mt-4 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#F5B400] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(245,180,0,0.18)] transition-all hover:bg-[#EAAA00] hover:shadow-[0_10px_24px_rgba(245,180,0,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span>{isLoading ? "Logging in..." : "Login"}</span>
              {!isLoading && (
                <span className="absolute right-5 text-[18px] font-normal transition-transform duration-200 group-hover:translate-x-1">→</span>
              )}
            </button>
          </form>

          <div className="mt-3">
            <GoogleSignInButton />
          </div>

          <p className="mt-4 text-center text-[12px] text-[#737B88]">
            Don't have an account?{" "}
            <Link to="/register" className="font-semibold text-[#D99700] hover:text-[#B87900]">
              Sign up
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
