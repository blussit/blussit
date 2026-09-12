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
      initial={{ opacity: 0, x: shouldReduceMotion ? 0 : -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="relative flex min-h-dvh flex-col overflow-x-hidden lg:h-dvh lg:overflow-hidden bg-[#FDF9EE] text-[#111111]"
    >
      <PublicNavbar />
      {/* =========================================================
          DESKTOP
          Figma-matched composition:
          - full viewport, no page scroll
          - photo occupies the left side
          - soft photo-to-cream fade in the center
          - compact, wider login card positioned on the right
          - NO bottom feature strip
      ========================================================= */}
      <section className="relative hidden min-h-0 w-full flex-1 overflow-hidden bg-[#FDF9EE] lg:block">
        {/* Left photography */}
        <div className="absolute inset-y-0 left-0 w-[58%] overflow-hidden">
          <img
            src="/login.webp"
            alt="BLUSSIT professional doorstep car care"
            className="absolute inset-0 h-full w-full object-cover object-[48%_center]"
          />

          {/* Gentle warm treatment */}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/5 via-transparent to-[#FDF9EE]/10" />

          {/* Wide feathered edge — no hard vertical split */}
          <div className="pointer-events-none absolute inset-y-0 right-0 w-[42%] bg-gradient-to-r from-transparent via-[#FDF9EE]/26 to-[#FDF9EE]" />
        </div>

        {/* Additional centre/right fade */}
      <div className="pointer-events-none absolute inset-y-0 left-[38%] right-0 bg-gradient-to-r from-transparent via-[#FDF9EE]/30 to-[#FDF9EE]/94" />
        {/* Figma-style cream background decorations */}
        <div className="pointer-events-none absolute right-0 top-0 h-full w-[42%] overflow-hidden">
          <div className="absolute -right-[170px] top-[13%] h-[560px] w-[560px] rounded-full border border-[#E9C875]/30" />
          <div className="absolute -right-[230px] top-[8%] h-[700px] w-[700px] rounded-full border border-[#E9C875]/18" />
          <div className="absolute right-[6%] top-[8%] h-[120px] w-[120px] opacity-60 [background-image:radial-gradient(#E8B642_1.35px,transparent_1.35px)] [background-size:16px_16px]" />
        </div>

        {/* Professional-care badge */}
        <div className="absolute left-[2.2vw] top-[2.2vh] z-30 flex items-center gap-2 rounded-full border border-white/75 bg-white/90 px-3 py-1.5 shadow-[0_7px_20px_rgba(30,27,20,0.09)] backdrop-blur-md">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#F1AA00]">
            <BadgeShieldIcon />
          </span>

          <div className="leading-[1.08]">
            <p className="text-[10.5px] font-black uppercase tracking-[0.04em] text-[#151515]">
              Professional Care
            </p>
            <p className="mt-0.5 text-[10.5px] font-bold uppercase tracking-[0.04em] text-[#687181]">
              At Your Doorstep
            </p>
          </div>
        </div>

        {/* =====================================================
            LOGIN CARD
            The important Figma fixes:
            1. Move card much farther right.
            2. Make it wider but substantially shorter.
            3. Reduce internal vertical spacing.
            4. Keep everything inside one viewport.
        ====================================================== */}
        <div className="absolute right-[12vw] top-1/2 z-30 w-[390px] max-w-[calc(100vw-48px)] -translate-y-1/2">
          <div className="flex h-full min-h-[560px] w-full flex-col rounded-[20px] border border-white/90 bg-white/[0.96] px-[26px] py-[30px] shadow-[0_16px_46px_rgba(39,33,20,0.09)] backdrop-blur-[4px]">
            {/* Logo */}
            <Link
              to="/"
              aria-label="Blussit home"
              className="mb-5 flex w-full flex-col items-center"
            >
              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="h-auto w-[142px] object-contain"
              />
              <span className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.24em] text-[#E8A900]">
                Clean Car. Clean Mind.
              </span>
            </Link>

            {/* Heading */}
            <div className="mb-5 text-left">
              <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">
                Welcome Back!
              </h1>
              <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">
                Login to continue to your dashboard.
              </p>
            </div>

            <form onSubmit={handleSubmit}>
              {/* Username */}
              <label className="block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Email or phone
                </span>

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
                    className="h-[43px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              {/* Password */}
              <label className="mt-3 block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Password
                </span>

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
                    className="h-[43px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-[48px] text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
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

              {/* Remember me + Forgot password */}
              <div className="mt-2.5 flex items-center justify-between">
                <label className="flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                    className="h-4 w-4 cursor-pointer rounded border-[#D9DDE3] text-[#E9AA00] focus:ring-[#F5B400]/30"
                  />
                  <span className="text-[12.5px] font-medium text-[#4A5057]">
                    Remember me
                  </span>
                </label>

                <Link
                  to="/forgot-password"
                  className="text-[12.5px] font-semibold text-[#D99400] transition-colors hover:text-[#B87800]"
                >
                  Forgot Password?
                </Link>
              </div>

              {/* Error */}
              {error && (
                <div className="mt-2.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">
                  {error}
                </div>
              )}

              {/* Login */}
              <button
                type="submit"
                disabled={isLoading}
                className="group relative mt-3.5 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#F5B400] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(245,180,0,0.18)] transition-all hover:bg-[#EAAA00] hover:shadow-[0_10px_24px_rgba(245,180,0,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span>{isLoading ? "Logging in..." : "Login"}</span>

                {!isLoading && (
                  <span className="absolute right-5 text-[18px] font-normal transition-transform duration-200 group-hover:translate-x-1">
                    →
                  </span>
                )}
              </button>
            </form>

            {/* Google — the real GSI button (hides itself when not configured) */}
            <div className="mt-3">
              <GoogleSignInButton />
            </div>

            {/* Register */}
            <p className="mt-4 text-center text-[12px] text-[#737B88]">
              Don't have an account?{" "}
              <Link
                to="/register"
                className="font-semibold text-[#D99700] hover:text-[#B87900]"
              >
                Sign up
              </Link>
            </p>
          </div>
        </div>
      </section>

      {/* =========================================================
          MOBILE
          Desktop changes above do not affect the mobile layout.
      ========================================================= */}
      <section className="relative flex flex-1 flex-col bg-[#FDF9EE] lg:hidden">
        <div className="relative h-[42dvh] min-h-[340px] shrink-0 overflow-hidden">
          <img
            src="/login.webp"
            alt="BLUSSIT professional doorstep car care"
            className="absolute inset-0 h-full w-full object-cover object-[center_center]"
          />

          <div className="absolute inset-0 bg-gradient-to-b from-black/5 via-transparent to-black/35" />

          <div className="absolute left-4 top-5 z-10 flex items-center gap-2 rounded-full border border-white/50 bg-white/90 px-3 py-2 shadow-[0_6px_18px_rgba(0,0,0,0.10)] backdrop-blur-sm">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#F1AA00]">
              <BadgeShieldIcon />
            </span>

            <div className="leading-[1.05]">
              <p className="text-[9px] font-black uppercase tracking-[0.04em] text-[#151515]">
                Professional Care
              </p>
              <p className="mt-0.5 text-[9px] font-black uppercase tracking-[0.04em] text-[#687181]">
                At Your Doorstep
              </p>
            </div>
          </div>
        </div>

        <div className="relative z-20 -mt-7 rounded-t-[26px] bg-[#FDF9EE] px-4 pb-7 pt-5 shadow-[0_-8px_30px_rgba(30,27,20,0.08)]">
          <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-[#D8D1C2]" />

          <div className="mx-auto w-full max-w-[430px] rounded-[18px] border border-[#ECE7D8] bg-white px-5 py-5 shadow-[0_8px_28px_rgba(30,27,20,0.06)]">
            <Link
              to="/"
              aria-label="Blussit home"
              className="mb-3 flex flex-col items-center"
            >
              <img
                src="/blussit-logo.png"
                alt="BLUSSIT"
                className="h-auto w-[135px] object-contain"
              />
              <span className="mt-0.5 text-[8px] font-bold uppercase tracking-[0.20em] text-[#E8A900]">
                Clean Car. Clean Mind.
              </span>
            </Link>

            <div className="mb-4 text-center">
              <h2 className="text-[23px] font-bold leading-[1.1] tracking-[-0.03em] text-[#111111]">
                Welcome Back!
              </h2>
              <p className="mt-1 text-[12px] leading-5 text-[#747C8A]">
                Login to continue to your dashboard.
              </p>
            </div>

            <form onSubmit={handleSubmit}>
              <label className="block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Email or phone
                </span>

                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <PersonIcon />
                  </div>

                  <input
                    type="text"
                    value={identifier}
                    onChange={(e) => setIdentifier(e.target.value)}
                    required
                    autoComplete="username"
                    placeholder="Enter email or phone number"
                    className="h-[44px] w-full rounded-[8px] border border-[#D9DDE3] bg-white pl-[44px] pr-3 text-[12px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              <label className="mt-2.5 block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Password
                </span>

                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <LockIcon />
                  </div>

                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                    placeholder="Enter your password"
                    className="h-[44px] w-full rounded-[8px] border border-[#D9DDE3] bg-white pl-[44px] pr-[44px] text-[12px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />

                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="absolute right-3.5 top-1/2 -translate-y-1/2"
                  >
                    {showPassword ? <EyeOffIcon /> : <EyeIcon />}
                  </button>
                </div>
              </label>

              <div className="mt-2.5 flex items-center justify-between">
                <label className="flex cursor-pointer items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                    className="h-3.5 w-3.5 cursor-pointer rounded border-[#D9DDE3] text-[#E9AA00] focus:ring-[#F5B400]/30"
                  />
                  <span className="text-[11px] font-medium text-[#4A5057]">
                    Remember me
                  </span>
                </label>

                <Link
                  to="/forgot-password"
                  className="text-[11px] font-semibold text-[#D99400]"
                >
                  Forgot Password?
                </Link>
              </div>

              {error && (
                <div className="mt-2.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading}
                className="group mt-3.5 flex h-[44px] w-full items-center justify-center gap-2 rounded-[8px] bg-[#F5B400] cursor-pointer text-[13px] font-bold text-white shadow-[0_6px_16px_rgba(245,180,0,0.16)] transition-all hover:bg-[#EAAA00] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isLoading ? "Logging in..." : "Login"}
                {!isLoading && (
                  <span className="text-[16px] font-normal transition-transform duration-200 group-hover:translate-x-1">
                    →
                  </span>
                )}
              </button>
            </form>

            <div className="mt-3">
              <GoogleSignInButton />
            </div>

            <p className="mt-3.5 text-center text-[11px] text-[#737B88]">
              Don't have an account?{" "}
              <Link
                to="/register"
                className="font-semibold text-[#D99700]"
              >
                Sign up
              </Link>
            </p>
          </div>
        </div>
      </section>
    </motion.main>
  );
}

/* =========================================================
   ICONS
========================================================= */

function BadgeShieldIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path
        d="M16 3L27 7V14.5C27 21.5 22.5 27 16 29C9.5 27 5 21.5 5 14.5V7L16 3Z"
        stroke="#fff"
        strokeWidth="2.4"
        strokeLinejoin="round"
      />
      <path
        d="M11 16L14.5 19.5L21.5 12.5"
        stroke="#fff"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PersonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="8" r="3.6" stroke="#111" strokeWidth="1.8" />
      <path
        d="M4.5 20C5.5 15.8 8.4 13.5 12 13.5C15.6 13.5 18.5 15.8 19.5 20"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect
        x="5"
        y="10"
        width="14"
        height="11"
        rx="2"
        stroke="#111"
        strokeWidth="1.8"
      />
      <path
        d="M8 10V7C8 4.8 9.8 3 12 3C14.2 3 16 4.8 16 7V10"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M2.5 12C4.5 7.8 7.8 5.5 12 5.5C16.2 5.5 19.5 7.8 21.5 12C19.5 16.2 16.2 18.5 12 18.5C7.8 18.5 4.5 16.2 2.5 12Z"
        stroke="#111"
        strokeWidth="1.8"
      />
      <circle cx="12" cy="12" r="3" stroke="#111" strokeWidth="1.8" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 3L21 21"
        stroke="#111"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
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

