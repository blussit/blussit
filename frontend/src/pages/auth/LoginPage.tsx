import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { OtpInput } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { authApi, guestAuthApi } from "../../api/auth";
import { getErrorMessage, tokenStorage } from "../../lib/api-client";
import { roleHomePath } from "../../lib/roleHome";
import { cleanMobileInput, validateIndianMobile } from "../../lib/validators";
import { ensureOtpWidget, otpErrorMessage, sendOtpCode, widgetVerifyOtp, type OtpChannel } from "../../lib/otpWidget";
import { GoogleSignInButton } from "../../components/shared/GoogleSignInButton";
import type { UserRole } from "../../types";

/**
 * Login (quick-booking model): customers sign in with their phone number
 * and a one-time code — there is no customer password any more, and
 * logging in is optional (only to see bookings/passes; booking itself
 * never needs it). Staff (admin/manager/captain) keep email + password
 * behind the "Staff login" switch.
 */
export default function LoginPage() {
  const { login, refreshUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const shouldReduceMotion = useReducedMotion();

  const [staff, setStaff] = useState(false);
  // -- customer (OTP) --
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [otpStep, setOtpStep] = useState<"phone" | "code">("phone");
  const [channel, setChannel] = useState<OtpChannel | null>(null);
  const [delivered, setDelivered] = useState<"whatsapp" | "sms" | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [smsAvailable, setSmsAvailable] = useState(false);
  const verifying = useRef(false);
  // -- staff (password) --
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    setError("");
  }, [staff, otpStep]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (otpStep === "code" && channel === "backend") void ensureOtpWidget().then(setSmsAvailable);
  }, [otpStep, channel]);

  const landAfterLogin = (role: UserRole) => {
    // Honor the deep-link ProtectedRoute captured (state.from) — but only
    // for the customer portal; staff always land on their own home.
    const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
    navigate(from && role === "customer" ? from : roleHomePath(role), { replace: true });
  };

  // order: the first send tries the backend first — its answer doubles as
  // the account check (no account / staff number stop right there) and it
  // costs nothing when WhatsApp can't reach the number, so the MSG91 widget
  // (SMS) takes over at once. Resend retries whichever channel delivered.
  const sendCode = async (e?: React.FormEvent, order: OtpChannel[] = ["backend", "widget"]) => {
    e?.preventDefault();
    if (isLoading) return;
    const normalized = validateIndianMobile(phone);
    if (!normalized) {
      setError("Enter a valid 10-digit mobile number.");
      return;
    }
    setError("");
    setIsLoading(true);
    try {
      let backendChannel: "whatsapp" | "sms" | undefined;
      const sent = await sendOtpCode(
        normalized,
        async () => {
          backendChannel = (await authApi.requestOtp(normalized)).channel;
        },
        order,
      );
      setChannel(sent.channel);
      setDelivered(sent.channel === "widget" ? "sms" : (backendChannel ?? null));
      setCooldown(sent.cooldown);
      setOtp("");
      setOtpStep("code");
    } catch (err) {
      const message = otpErrorMessage(err);
      setError(/no account/i.test(message) ? "No account for this number yet — your account is created automatically the first time you book." : message);
      setCooldown(0);
    } finally {
      setIsLoading(false);
    }
  };

  const verifyCode = async (code = otp) => {
    const clean = code.trim();
    if (clean.length < 6 || verifying.current) return;
    verifying.current = true;
    const normalized = validateIndianMobile(phone) || phone.trim();
    setError("");
    setIsLoading(true);
    try {
      const payload = channel === "widget" ? { phone: normalized, access_token: await widgetVerifyOtp(clean) } : { phone: normalized, otp: clean };
      const result = await guestAuthApi.otpLogin(payload);
      tokenStorage.set(result.access_token, result.refresh_token);
      const user = await refreshUser();
      landAfterLogin(user?.role || result.user.role);
    } catch (err) {
      setError(otpErrorMessage(err));
      setOtp("");
    } finally {
      verifying.current = false;
      setIsLoading(false);
    }
  };

  const changeNumber = () => {
    setOtpStep("phone");
    setOtp("");
    setChannel(null);
    setDelivered(null);
    setCooldown(0);
  };

  const staffSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      const user = await login(identifier, password);
      landAfterLogin(user.role);
    } catch (err) {
      const message = getErrorMessage(err);
      setError(/invalid credentials/i.test(message) ? "Incorrect email/phone or password" : message);
    } finally {
      setIsLoading(false);
    }
  };

  const inputClass =
    "h-[44px] w-full rounded-[9px] border border-[#D9DDE3] bg-white px-4 text-[13px] text-[#111111] outline-none transition-all placeholder:text-[#9AA1AD] focus:border-[#E9AA00] focus:ring-4 focus:ring-[#F5B400]/10";
  const ctaClass =
    "group relative mt-4 flex h-[48px] w-full items-center justify-center rounded-[10px] bg-[#F5B400] cursor-pointer text-[14px] font-bold text-white shadow-[0_8px_20px_rgba(245,180,0,0.18)] transition-all hover:bg-[#EAAA00] disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <motion.main
      initial={{ opacity: 0, y: shouldReduceMotion ? 0 : 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="flex min-h-dvh flex-col bg-[#FDF9EE] text-[#111111]"
    >
      <PublicNavbar />

      <section className="flex flex-1 items-center justify-center px-4 py-10">
        <div className="w-full max-w-[420px] rounded-[20px] border border-[#ECE7D8] bg-white px-6 py-8 shadow-[0_16px_46px_rgba(39,33,20,0.08)] sm:px-8 sm:py-9">
          <Link to="/" aria-label="Blussit home" className="mb-6 flex w-full flex-col items-center">
            <img src="/img/blussit-logo-480.webp" alt="BLUSSIT" className="h-auto w-[142px] object-contain" />
            <span className="mt-0.5 whitespace-nowrap text-[7px] font-bold uppercase tracking-[0.12em] text-[#E8A900]">
              Premium Car Wash At Your Doorstep.
            </span>
          </Link>

          {!staff ? (
            <>
              <div className="mb-6 text-center">
                <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">
                  {otpStep === "phone" ? "Welcome Back!" : "Enter Your Code"}
                </h1>
                <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">
                  {otpStep === "phone"
                    ? "Log in with your mobile number to see your bookings and passes."
                    : `We sent a 6-digit code to +91 ${phone}${delivered === "sms" ? " by SMS" : delivered === "whatsapp" ? " on WhatsApp" : ""}.`}
                </p>
              </div>

              {otpStep === "phone" ? (
                <form onSubmit={sendCode}>
                  <label className="block">
                    <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Mobile number</span>
                    <input
                      type="tel"
                      inputMode="numeric"
                      value={phone}
                      onChange={(e) => setPhone(cleanMobileInput(e.target.value))}
                      required
                      autoComplete="tel"
                      placeholder="10-digit mobile number"
                      className={inputClass}
                    />
                  </label>
                  {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">{error}</div>}
                  <button type="submit" disabled={isLoading} className={ctaClass}>
                    <span>{isLoading ? "Sending code..." : "Send Code"}</span>
                    {!isLoading && <span className="absolute right-5 text-[18px] font-normal transition-transform duration-200 group-hover:translate-x-1">→</span>}
                  </button>
                </form>
              ) : (
                <div>
                  <OtpInput value={otp} onChange={setOtp} autoFocus disabled={isLoading} onComplete={(code) => void verifyCode(code)} />
                  {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">{error}</div>}
                  <button type="button" disabled={isLoading || otp.trim().length < 6} onClick={() => void verifyCode()} className={ctaClass}>
                    <span>{isLoading ? "Verifying..." : "Log In"}</span>
                  </button>
                  <div className="mt-3 flex items-center justify-between text-[12px]">
                    {cooldown > 0 ? (
                      <span className="font-medium text-[#737B88]">Resend in {cooldown}s</span>
                    ) : (
                      <span className="flex items-center gap-3">
                        <button
                          type="button"
                          className="font-semibold text-[#D99400] hover:text-[#B87800] disabled:opacity-50"
                          onClick={() => void sendCode(undefined, channel === "widget" ? ["widget", "backend"] : ["backend", "widget"])}
                          disabled={isLoading}
                        >
                          Resend code
                        </button>
                        {channel === "backend" && smsAvailable && (
                          <button type="button" className="font-semibold text-[#D99400] hover:text-[#B87800] disabled:opacity-50" onClick={() => void sendCode(undefined, ["widget"])} disabled={isLoading}>
                            Get it by SMS
                          </button>
                        )}
                      </span>
                    )}
                    <button type="button" className="font-medium text-[#737B88] hover:text-[#111]" onClick={changeNumber} disabled={isLoading}>
                      Change number
                    </button>
                  </div>
                </div>
              )}

              <div className="mt-3">
                <GoogleSignInButton />
              </div>

              <p className="mt-4 text-center text-[12px] text-[#737B88]">
                New here? No sign-up needed —{" "}
                <Link to="/book" className="font-semibold text-[#D99700] hover:text-[#B87900]">
                  just book a wash
                </Link>{" "}
                and your account is created for you.
              </p>
              <p className="mt-2 text-center text-[11px] text-[#9AA1AD]">
                <button type="button" onClick={() => setStaff(true)} className="underline underline-offset-2 hover:text-[#111]">
                  Staff login
                </button>
              </p>
            </>
          ) : (
            <>
              <div className="mb-6 text-center">
                <h1 className="text-[26px] font-bold leading-[1.08] tracking-[-0.03em] text-[#111111]">Staff Login</h1>
                <p className="mt-1 text-[13px] leading-5 text-[#747C8A]">Admin, manager and captain accounts.</p>
              </div>
              <form onSubmit={staffSubmit}>
                <label className="block">
                  <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Email or phone</span>
                  <input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required autoComplete="username" placeholder="Enter email or phone number" className={inputClass} />
                </label>
                <label className="mt-3 block">
                  <span className="mb-1 block text-[12.5px] font-semibold text-[#171717]">Password</span>
                  <div className="relative">
                    <input
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      required
                      autoComplete="current-password"
                      placeholder="Enter your password"
                      className={`${inputClass} pr-[70px]`}
                    />
                    <button type="button" onClick={() => setShowPassword((v) => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] font-semibold text-[#4A5057]">
                      {showPassword ? "Hide" : "Show"}
                    </button>
                  </div>
                </label>
                <div className="mt-3 text-right">
                  <Link to="/forgot-password" className="text-[12.5px] font-semibold text-[#D99400] transition-colors hover:text-[#B87800]">
                    Forgot Password?
                  </Link>
                </div>
                {error && <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] font-medium text-red-700">{error}</div>}
                <button type="submit" disabled={isLoading} className={ctaClass}>
                  <span>{isLoading ? "Logging in..." : "Login"}</span>
                </button>
              </form>
              <p className="mt-4 text-center text-[11px] text-[#9AA1AD]">
                <button type="button" onClick={() => setStaff(false)} className="underline underline-offset-2 hover:text-[#111]">
                  Customer login (OTP)
                </button>
              </p>
            </>
          )}
        </div>
      </section>
    </motion.main>
  );
}
