import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { authApi, otpWidgetApi } from "../../api/auth";
import { CheckCircle2 } from "lucide-react";
import { otpErrorMessage, sendOtpCode, widgetVerifyOtp, type OtpChannel } from "../../lib/otpWidget";
import { validateIndianMobile } from "../../lib/validators";


export default function ForgotPasswordPage() {
  const shouldReduceMotion = useReducedMotion();

  const [step, setStep] = useState<"request" | "reset" | "done">("request");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [channel, setChannel] = useState<OtpChannel | null>(null);
  const [delivered, setDelivered] = useState<"whatsapp" | "sms" | null>(null);
  const [cooldown, setCooldown] = useState(0);
  // Only a typed phone can fall back to the MSG91 widget (SMS) — for an
  // email we don't know (and must not reveal) the number on file.
  const phone = validateIndianMobile(identifier);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const sendCode = async (order?: OtpChannel[]) => {
    if (isLoading) return;
    setError("");
    setIsLoading(true);
    try {
      let backendChannel: "whatsapp" | "sms" | undefined;
      const sent = await sendOtpCode(
        phone || "",
        async () => {
          backendChannel = (await authApi.forgotPassword(phone || identifier.trim())).channel;
        },
        order ?? (phone ? ["backend", "widget"] : ["backend"]),
      );
      setChannel(sent.channel);
      setDelivered(sent.channel === "widget" ? "sms" : (backendChannel ?? null));
      setCooldown(sent.cooldown);
      setOtp("");
      setStep("reset");
    } catch (err) {
      setError(otpErrorMessage(err));
      setCooldown(0);
    } finally {
      setIsLoading(false);
    }
  };

  const requestOtp = (e: React.FormEvent) => {
    e.preventDefault();
    void sendCode();
  };

  const resetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isLoading) return;
    setError("");
    setIsLoading(true);
    try {
      if (channel === "widget" && phone) {
        await otpWidgetApi.resetPassword({ access_token: await widgetVerifyOtp(otp.trim()), phone, new_password: newPassword });
      } else {
        await authApi.resetPassword({ identifier: phone || identifier.trim(), otp: otp.trim(), new_password: newPassword });
      }
      setStep("done");
    } catch (err) {
      setError(otpErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  const sentTo = delivered === "sms" ? "by SMS to" : delivered === "whatsapp" ? "on WhatsApp to" : "to";

  return (
    <motion.main 
      initial={{ opacity: 0, x: shouldReduceMotion ? 0 : -10 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="relative flex min-h-dvh flex-col overflow-x-hidden lg:h-dvh lg:overflow-hidden bg-[#F4F8FF] text-[#0E1A33]"
    >
      <PublicNavbar />
      {/* =========================================================
          DESKTOP
          Figma-matched composition:
          - full viewport, no page scroll
          - photo occupies the left side
          - soft photo-to-ground fade in the center
          - compact, wider login card positioned on the right
          - NO bottom feature strip
      ========================================================= */}
      <section className="relative min-h-0 w-full flex-1 overflow-hidden bg-[#F4F8FF]">
        {/* Left photography */}
        <div className="absolute inset-y-0 left-0 w-[58%] overflow-hidden">
          <img
            src="/login.webp"
            alt="BLUSSIT professional doorstep car care"
            className="absolute inset-0 h-full w-full object-cover object-[48%_center]"
          />

          {/* Gentle warm treatment */}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/5 via-transparent to-[#F4F8FF]/10" />

          {/* Wide feathered edge — no hard vertical split */}
          <div className="pointer-events-none absolute inset-y-0 right-0 w-[42%] bg-gradient-to-r from-transparent via-[#F4F8FF]/26 to-[#F4F8FF]" />
        </div>

        {/* Additional centre/right fade */}
      <div className="pointer-events-none absolute inset-y-0 left-[38%] right-0 bg-gradient-to-r from-transparent via-[#F4F8FF]/30 to-[#F4F8FF]/94" />
        {/* Soft background decorations */}
        <div className="pointer-events-none absolute right-0 top-0 h-full w-[42%] overflow-hidden">
          <div className="absolute -right-[170px] top-[13%] h-[560px] w-[560px] rounded-full border border-[#0A66F0]/12" />
          <div className="absolute -right-[230px] top-[8%] h-[700px] w-[700px] rounded-full border border-[#0A66F0]/8" />
          <div className="absolute right-[6%] top-[8%] h-[120px] w-[120px] opacity-60 [background-image:radial-gradient(#9DBDF5_1.35px,transparent_1.35px)] [background-size:16px_16px]" />
        </div>

        {/* Professional-care badge */}
        <div className="absolute left-[2.2vw] top-[2.2vh] z-30 flex items-center gap-2 rounded-full border border-white/75 bg-white/90 px-3 py-1.5 shadow-[0_7px_20px_rgba(30,27,20,0.09)] backdrop-blur-md">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#0A66F0]">
            <BadgeShieldIcon />
          </span>

          <div className="leading-[1.08]">
            <p className="text-[11px] font-black uppercase tracking-[0.04em] text-[#0E1A33]">
              Professional Care
            </p>
            <p className="mt-0.5 text-[11px] font-bold uppercase tracking-[0.04em] text-[#5F6878]">
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
        {/* Phones/tablets: in the page flow (the section grows with it) — an
            absolutely centred card was clipped by the section on short
            screens and when the keyboard opened. Desktop keeps the Figma
            placement. */}
        <div className="relative z-30 mx-auto mb-8 mt-[76px] w-[calc(100%-32px)] max-w-[390px] lg:absolute lg:right-[12vw] lg:top-1/2 lg:mx-0 lg:my-0 lg:w-[390px] lg:max-w-[calc(100vw-48px)] lg:-translate-y-1/2">
          <div className="flex w-full flex-col rounded-[20px] border border-white/90 bg-white/[0.96] px-[26px] py-[30px] shadow-[0_16px_46px_rgba(39,33,20,0.09)] backdrop-blur-[4px]">
            {/* Logo */}
            <Link
              to="/"
              aria-label="Blussit home"
              className="mb-5 flex w-full flex-col items-center"
            >
              <img
                src="/img/blussit-logo-480.webp"
                alt="BLUSSIT"
                className="h-auto w-[142px] object-contain"
              />
              <span className="mt-1 text-center text-[11px] font-bold uppercase leading-tight tracking-[0.06em] text-[#0A66F0]">
                Premium Car Wash At Doorstep.
              </span>
            </Link>
            {step === "request" && (
              <>
                <div className="mb-5 text-left">
                  <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#0E1A33]">
                    Reset Your Password
                  </h1>
                  <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">
                    Enter your email or phone to receive a reset code.
                  </p>
                </div>
                <form onSubmit={requestOtp}>
                  <label className="block">
                    <span className="mb-1 block text-[12.5px] font-semibold text-[#0E1A33]">
                      Email Or Phone Number
                    </span>
                    <div className="relative">
                      <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                        <PersonIcon />
                      </div>
                      <input
                        type="text"
                        value={identifier}
                        onChange={(e) => setIdentifier(e.target.value.toLowerCase())}
                        required
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        placeholder="Enter your email or phone"
                        className="h-[43px] w-full rounded-[9px] border border-[#D8E6F7] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10"
                      />
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
                    className="group relative mt-4 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#0A66F0] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(10,102,240,0.18)] transition-all hover:bg-[#0857D0] hover:shadow-[0_10px_24px_rgba(10,102,240,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span>{isLoading ? "Sending…" : "Send Reset Code"}</span>
                  </button>
                </form>
              </>
            )}

            {step === "reset" && (
              <>
                <div className="mb-5 text-left">
                  <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#0E1A33]">
                    Enter Reset Code
                  </h1>
                  <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">
                    We sent a 6-digit code {sentTo} {phone ? <span className="font-medium">+91 {phone}</span> : <>the number on file for <span className="font-medium">{identifier}</span></>}.
                  </p>
                </div>
                <form onSubmit={resetPassword}>
                  <label className="block">
                    <span className="mb-1 block text-[12.5px] font-semibold text-[#0E1A33]">
                      Reset Code
                    </span>
                    <div className="relative">
                      <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                        <PersonIcon />
                      </div>
                      <input
                        type="text"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        value={otp}
                        onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                        required
                        placeholder="6-digit code"
                        className="h-[43px] w-full rounded-[9px] border border-[#D8E6F7] bg-white pl-[48px] pr-4 text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10"
                      />
                    </div>
                  </label>
                  <label className="mt-3 block">
                    <span className="mb-1 block text-[12.5px] font-semibold text-[#0E1A33]">
                      New Password
                    </span>
                    <div className="relative">
                      <div className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2">
                        <LockIcon />
                      </div>
                      <input
                        type={showPassword ? "text" : "password"}
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        required
                        placeholder="At least 8 characters"
                        className="h-[43px] w-full rounded-[9px] border border-[#D8E6F7] bg-white pl-[48px] pr-[48px] text-[12.5px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((value) => !value)}
                        className="absolute right-4 top-1/2 -translate-y-1/2 text-[#222] transition-opacity hover:opacity-60"
                      >
                        {showPassword ? <EyeOffIcon /> : <EyeIcon />}
                      </button>
                    </div>
                    {newPassword.length > 0 && newPassword.length < 8 && (
                      <span className="mt-1 block text-[11px] font-medium text-[var(--color-error)]">Enter at least 8 characters.</span>
                    )}
                  </label>
                  {error && (
                    <div className="mt-2.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">
                      {error}
                    </div>
                  )}
                  <div className="mt-3 flex items-center justify-between text-[12px]">
                    {cooldown > 0 ? (
                      <span className="font-medium text-[#737B88]">Resend in {cooldown}s</span>
                    ) : (
                      <button
                        type="button"
                        className="font-semibold text-[#0A66F0] hover:text-[#0857D0] disabled:opacity-50"
                        disabled={isLoading}
                        onClick={() => void sendCode(channel === "widget" ? ["widget", "backend"] : undefined)}
                      >
                        Resend Code
                      </button>
                    )}
                    <button
                      type="button"
                      className="font-medium text-[#737B88] hover:text-[#111]"
                      disabled={isLoading}
                      onClick={() => {
                        setStep("request");
                        setOtp("");
                        setError("");
                        setChannel(null);
                        setCooldown(0);
                      }}
                    >
                      Change Email / Phone
                    </button>
                  </div>
                  <button
                    type="submit"
                    disabled={isLoading || otp.length < 6 || newPassword.length < 8}
                    className="group relative mt-4 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#0A66F0] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(10,102,240,0.18)] transition-all hover:bg-[#0857D0] hover:shadow-[0_10px_24px_rgba(10,102,240,0.23)] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span>{isLoading ? "Resetting…" : "Reset Password"}</span>
                  </button>
                </form>
              </>
            )}

            {step === "done" && (
              <div className="text-center pb-2">
                <CheckCircle2 className="mx-auto mt-2 h-10 w-10 text-[#009A65]" />
                <h1 className="mt-4 text-[24px] font-bold leading-[1.08] tracking-[-0.03em] text-[#0E1A33]">
                  Password Reset
                </h1>
                <p className="mt-2 text-[13px] leading-5 text-[#747C8A]">
                  You can now log in with your new password.
                </p>
                <Link to="/login">
                  <button className="group relative mt-6 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#0A66F0] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(10,102,240,0.18)] transition-all hover:bg-[#0857D0] hover:shadow-[0_10px_24px_rgba(10,102,240,0.23)]">
                    <span>Back To Login</span>
                  </button>
                </Link>
              </div>
            )}

            {step !== "done" && (
              <p className="mt-5 text-center text-[12px] text-[#737B88]">
                Remember your password?{" "}
                <Link
                  to="/login"
                  className="font-semibold text-[#0A66F0] hover:text-[#0857D0]"
                >
                  Log In
                </Link>
              </p>
            )}

            
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
