/**
 * MSG91 OTP-widget loader (exposeMethods mode — no MSG91 popup; we drive
 * sendOtp/retryOtp/verifyOtp from our own UI). The widget delivers the
 * OTP over MSG91's channels (SMS/WhatsApp/email) and verification yields
 * an access token that ONLY our backend can bless (server-side
 * verifyAccessToken with identifier binding) — the frontend never treats
 * widget success alone as proof.
 *
 * When the backend reports the widget as not configured, callers fall
 * back to the classic backend-generated OTP flow.
 */
import { otpWidgetApi } from "../api/auth";

declare global {
  interface Window {
    initSendOTP?: (config: Record<string, unknown>) => void;
    sendOtp?: (identifier: string, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
    retryOtp?: (channel: string | null, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
    verifyOtp?: (otp: string, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
  }
}

let loader: Promise<boolean> | null = null;

/** Loads the widget script once. Resolves false when the widget isn't
 * configured on the backend (caller should use the classic OTP flow). */
export function ensureOtpWidget(): Promise<boolean> {
  if (!loader) {
    loader = (async () => {
      try {
        const cfg = await otpWidgetApi.config();
        if (!cfg.enabled || !cfg.widget_id || !cfg.token_auth) return false;
        // Primary + fallback hosts, per MSG91's own embed snippet.
        const urls = ["https://verify.msg91.com/otp-provider.js", "https://verify.phone91.com/otp-provider.js"];
        await new Promise<void>((resolve, reject) => {
          const attempt = (i: number) => {
            const script = document.createElement("script");
            script.src = urls[i];
            script.async = true;
            script.onload = () => {
              // The widget's captcha (when enabled in the MSG91 dashboard)
              // needs a live element to render into — without one, sendOtp
              // silently never resolves. Invisible captcha auto-solves in
              // this floating container; a visible one appears bottom-right.
              let slot = document.getElementById("msg91-captcha-slot");
              if (!slot) {
                slot = document.createElement("div");
                slot.id = "msg91-captcha-slot";
                slot.style.cssText = "position:fixed;bottom:12px;right:12px;z-index:9999;";
                document.body.appendChild(slot);
              }
              window.initSendOTP?.({
                widgetId: cfg.widget_id,
                tokenAuth: cfg.token_auth,
                exposeMethods: true,
                captchaRenderId: "msg91-captcha-slot",
                success: () => undefined,
                failure: () => undefined,
              });
              resolve();
            };
            script.onerror = () => (i + 1 < urls.length ? attempt(i + 1) : reject(new Error("widget script failed")));
            document.body.appendChild(script);
          };
          attempt(0);
        });
        // initSendOTP registers the window methods asynchronously.
        for (let i = 0; i < 40 && !window.sendOtp; i++) await new Promise((r) => setTimeout(r, 125));
        return !!window.sendOtp;
      } catch {
        return false;
      }
    })();
  }
  return loader;
}

function extractError(e: unknown): string {
  if (typeof e === "string") return e;
  const anyE = e as { message?: string } | undefined;
  return anyE?.message || "Couldn't send the code — please try again.";
}

/** Sends the OTP to a 10-digit Indian number via the widget. */
export function widgetSendOtp(phone10: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!window.sendOtp) return reject(new Error("OTP service unavailable"));
    window.sendOtp(
      `91${phone10}`,
      () => resolve(),
      (e) => reject(new Error(extractError(e))),
    );
  });
}

/** Verifies the typed OTP; resolves with the MSG91 access token, which the
 * caller must submit to the backend for the real (server-side) check. */
export function widgetVerifyOtp(otp: string): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!window.verifyOtp) return reject(new Error("OTP service unavailable"));
    window.verifyOtp(
      otp,
      (data) => {
        const d = data as { message?: string } | string;
        const token = typeof d === "string" ? d : d?.message;
        if (token && String(token).length > 10) resolve(String(token));
        else reject(new Error("Verification failed — check the code and try again."));
      },
      (e) => reject(new Error(typeof e === "string" ? e : "Incorrect code — please try again.")),
    );
  });
}

export function widgetRetryOtp(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!window.retryOtp) return reject(new Error("OTP service unavailable"));
    window.retryOtp(null, () => resolve(), () => reject(new Error("Couldn't resend — try again in a minute.")));
  });
}
