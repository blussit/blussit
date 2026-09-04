import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";

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
      initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="relative min-h-dvh overflow-x-hidden lg:h-dvh lg:overflow-hidden bg-[#FDF9EE] text-[#111111]"
    >
      {/* =========================================================
          DESKTOP
          Figma-matched composition:
          - full viewport, no page scroll
          - photo occupies the left side
          - soft photo-to-cream fade in the center
          - compact, wider login card positioned on the right
          - NO bottom feature strip
      ========================================================= */}
      <section className="relative hidden h-dvh w-full overflow-hidden bg-[#FDF9EE] lg:block">
        {/* Left photography */}
        <div className="absolute inset-y-0 left-0 w-[58%] overflow-hidden">
          <img
            src="/login.png"
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
          <div className="flex h-full min-h-[520px] w-full flex-col rounded-[20px] border border-white/90 bg-white/[0.96] px-[26px] py-[24px] shadow-[0_16px_46px_rgba(39,33,20,0.09)] backdrop-blur-[4px]">
            {/* Logo */}
            <Link
              to="/"
              aria-label="Blussit home"
              className="mb-4 flex w-full flex-col items-center"
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
            <div className="mb-4 text-left">
              <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">
                Create your account
              </h1>
              <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">
                Book your first doorstep service in minutes.
              </p>
            </div>

            <form onSubmit={handleSubmit}>
              {/* Full Name */}
              <label className="block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Full name
                </span>
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
                    className="h-[43px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              {/* Email */}
              <label className="mt-2 block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Email
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                    <MailIcon />
                  </div>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    placeholder="Enter your email"
                    className="h-[43px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              {/* Phone */}
              <label className="mt-2 block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Phone number
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                    <PhoneIcon />
                  </div>
                  <input
                    type="text"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    placeholder="Enter your phone number"
                    className="h-[43px] w-full rounded-[9px] border border-[#D9DDE3] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              {/* Password */}
              <label className="mt-2 block">
                <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">
                  Password
                </span>
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
                className="group relative mt-3 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#F5B400] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(245,180,0,0.18)] transition-all hover:bg-[#EAAA00] hover:shadow-[0_10px_24px_rgba(245,180,0,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span>{isLoading ? "Logging in..." : "Login"}</span>

                {!isLoading && (
                  <span className="absolute right-5 text-[18px] font-normal transition-transform duration-200 group-hover:translate-x-1">
                    →
                  </span>
                )}
              </button>
            </form>

            {/* Divider */}
            <div className="my-3.5 flex items-center gap-3">
              <div className="h-px flex-1 bg-[#E5E6E8]" />
              <span className="whitespace-nowrap text-[11px] text-[#858C98]">
                or continue with
              </span>
              <div className="h-px flex-1 bg-[#E5E6E8]" />
            </div>

            {/* Google */}
            <button
              type="button"
              className="flex h-[46px] w-full items-center justify-center gap-2.5 rounded-[10px] border border-[#DCDFE4] bg-white text-[13px] font-semibold text-[#181818] transition-all hover:border-[#C9CDD3] hover:bg-[#FAFAFA]"
            >
              <GoogleIcon />
              <span>Continue with Google</span>
            </button>

            {/* Register */}
            <p className="mt-3.5 text-center text-[12px] text-[#737B88]">
              Already have an account?{" "}
              <Link
                to="/login"
                className="font-semibold text-[#D99700] hover:text-[#B87900]"
              >
                Log in
              </Link>
            </p>
          </div>
        </div>
      </section>

      {/* =========================================================
          MOBILE
          Desktop changes above do not affect the mobile layout.
      ========================================================= */}
      <section className="relative flex min-h-dvh flex-col bg-[#FDF9EE] lg:hidden">
        <div className="relative h-[42dvh] min-h-[340px] shrink-0 overflow-hidden">
          <img
            src="/login.png"
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

          <div className="mx-auto w-full max-w-[430px] rounded-[18px] border border-[#ECE7D8] bg-white px-5 py-4 shadow-[0_8px_28px_rgba(30,27,20,0.06)]">
            <Link
              to="/"
              aria-label="Blussit home"
              className="mb-2.5 flex flex-col items-center"
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

            <div className="mb-3 text-center">
              <h2 className="text-[23px] font-bold leading-[1.1] tracking-[-0.03em] text-[#111111]">
                Create your account
              </h2>
              <p className="mt-1 text-[12px] leading-5 text-[#747C8A]">
                Book your first doorstep service in minutes.
              </p>
            </div>

            <form onSubmit={handleSubmit}>
              <label className="block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Full name
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <PersonIcon />
                  </div>
                  <input
                    type="text"
                    value={form.full_name}
                    onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                    required
                    placeholder="Enter your full name"
                    className="h-[44px] w-full rounded-[8px] border border-[#D9DDE3] bg-white pl-[44px] pr-3 text-[12px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              <label className="mt-2 block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Email
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <MailIcon />
                  </div>
                  <input
                    type="email"
                    value={form.email}
                    onChange={(e) => setForm({ ...form, email: e.target.value })}
                    placeholder="Enter your email"
                    className="h-[44px] w-full rounded-[8px] border border-[#D9DDE3] bg-white pl-[44px] pr-3 text-[12px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              <label className="mt-2 block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Phone number
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <PhoneIcon />
                  </div>
                  <input
                    type="text"
                    value={form.phone}
                    onChange={(e) => setForm({ ...form, phone: e.target.value })}
                    placeholder="Enter your phone number"
                    className="h-[44px] w-full rounded-[8px] border border-[#D9DDE3] bg-white pl-[44px] pr-3 text-[12px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10"
                  />
                </div>
              </label>

              <label className="mt-2 block">
                <span className="mb-1.5 block text-[11px] font-semibold text-[#171717]">
                  Password
                </span>
                <div className="relative">
                  <div className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2">
                    <LockIcon />
                  </div>
                  <input
                    type={showPassword ? "text" : "password"}
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                    required
                    placeholder="At least 8 characters"
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

              

              {error && (
                <div className="mt-2.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={isLoading}
                className="group mt-3 flex h-[44px] w-full items-center justify-center gap-2 rounded-[8px] bg-[#F5B400] cursor-pointer text-[13px] font-bold text-white shadow-[0_6px_16px_rgba(245,180,0,0.16)] transition-all hover:bg-[#EAAA00] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isLoading ? "Logging in..." : "Login"}
                {!isLoading && (
                  <span className="text-[16px] font-normal transition-transform duration-200 group-hover:translate-x-1">
                    →
                  </span>
                )}
              </button>
            </form>

            <div className="my-3 flex items-center gap-2.5">
              <div className="h-px flex-1 bg-[#E5E6E8]" />
              <span className="whitespace-nowrap text-[10px] text-[#858C98]">
                or continue with
              </span>
              <div className="h-px flex-1 bg-[#E5E6E8]" />
            </div>

            <button
              type="button"
              className="flex h-[42px] w-full items-center justify-center gap-2.5 rounded-[8px] border border-[#DCDFE4] bg-white text-[12px] font-semibold text-[#181818] transition-all hover:bg-[#FAFAFA]"
            >
              <GoogleIcon />
              <span>Continue with Google</span>
            </button>

            <p className="mt-3 text-center text-[11px] text-[#737B88]">
              Already have an account?{" "}
              <Link
                to="/login"
                className="font-semibold text-[#D99700]"
              >
                Log in
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

function GoogleIcon() {
  return (
    <svg width="19" height="19" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M21.35 12.23C21.35 11.47 21.28 10.92 21.13 10.35H12V13.78H17.84C17.72 14.63 17.14 15.91 15.82 16.77L15.8 16.88L18.55 19.01L18.74 19.03C20.45 17.45 21.35 15.12 21.35 12.23Z"
        fill="#4285F4"
      />
      <path
        d="M12 21.5C14.59 21.5 16.77 20.65 18.4 19.18L15.7 17.08C14.98 17.58 14 17.93 12 17.93C9.52 17.93 7.41 16.29 6.75 14.02L6.64 14.03L3.78 16.24L3.74 16.35C5.36 19.4 8.49 21.5 12 21.5Z"
        fill="#34A853"
      />
      <path
        d="M6.75 14.02C6.57 13.47 6.47 12.88 6.47 12.27C6.47 11.66 6.57 11.07 6.74 10.52L6.74 10.4L3.84 8.15L3.74 8.2C3.18 9.33 2.85 10.6 2.85 12C2.85 13.4 3.18 14.67 3.74 15.8L6.75 14.02Z"
        fill="#FBBC05"
      />
      <path
        d="M12 6.07C13.8 6.07 15.02 6.84 15.72 7.48L18.45 4.81C16.76 3.24 14.59 2.27 12 2.27C8.49 2.27 5.36 4.38 3.74 7.43L6.74 9.64C7.41 7.37 9.52 6.07 12 6.07Z"
        fill="#EA4335"
      />
    </svg>
  );
}

function MailIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3 8L10.89 13.26C11.56 13.7 12.44 13.7 13.11 13.26L21 8M5 19H19C20.1 19 21 18.1 21 17V7C21 5.9 20.1 5 19 5H5C3.9 5 3 5.9 3 7V17C3 18.1 3.9 19 5 19Z" stroke="#111" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

function PhoneIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M22 16.92V19.92C22 20.45 21.56 20.91 21.03 20.92C18.11 20.98 15.32 20.08 12.91 18.42C10.66 16.89 8.79 15.02 7.26 12.77C5.6 10.36 4.71 7.57 4.76 4.65C4.77 4.12 5.21 3.68 5.74 3.68H8.74C9.22 3.68 9.63 4.04 9.71 4.51C9.83 5.37 10.05 6.2 10.36 7C10.48 7.31 10.4 7.66 10.17 7.89L8.44 9.62C9.86 12.11 11.89 14.14 14.38 15.56L16.11 13.83C16.34 13.6 16.69 13.52 17 13.64C17.8 13.95 18.63 14.17 19.49 14.29C19.96 14.37 20.32 14.78 20.32 15.26V16.92H22Z" stroke="#111" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}
