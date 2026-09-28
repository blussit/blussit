/**
 * MSG91 OTP-widget loader (exposeMethods mode — no MSG91 popup; we drive
 * sendOtp/retryOtp/verifyOtp from our own UI). The widget delivers the
 * OTP over MSG91's channels (SMS/WhatsApp/email) and verification yields
 * an access token that ONLY our backend can bless (server-side
 * verifyAccessToken with identifier binding) — the frontend never treats
 * widget success alone as proof.
 *
 * When the backend reports the widget as not configured, or its script
 * can't load, callers fall back to the classic backend-generated OTP flow.
 * sendOtpCode() is the one place that picks and falls back between the two.
 */
import axios from "axios";
import { otpWidgetApi } from "../api/auth";
import { getErrorMessage } from "./api-client";

declare global {
  interface Window {
    initSendOTP?: (config: Record<string, unknown>) => void;
    sendOtp?: (identifier: string, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
    retryOtp?: (channel: string | null, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
    verifyOtp?: (otp: string, ok?: (d: unknown) => void, fail?: (e: unknown) => void) => void;
  }
}

/** Same as the backend's resend cooldown (AuthService._OTP_RESEND_COOLDOWN_SECONDS). */
export const OTP_RESEND_SECONDS = 30;
export type OtpChannel = "backend" | "widget";

// A hung script/callback must end in the backend fallback or a clear error,
// never an endless "Sending…".
const SCRIPT_TIMEOUT_MS = 10_000;
const SEND_TIMEOUT_MS = 25_000;
const VERIFY_TIMEOUT_MS = 20_000;

type OtpConfig = Awaited<ReturnType<typeof otpWidgetApi.config>>;
let configPromise: Promise<OtpConfig> | null = null;
let scriptPromise: Promise<void> | null = null;
let loader: Promise<boolean> | null = null;

const getOtpConfig = () =>
  (configPromise ??= otpWidgetApi.config().catch((err) => {
    configPromise = null; // a network blip must not disable the widget for the whole visit
    throw err;
  }));

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  return Promise.race([
    promise,
    new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

/** True once the backend has an approved WhatsApp OTP template — from then
 * on WhatsApp leads; until then the MSG91 widget (SMS) is the primary. */
export async function whatsappIsPrimary(): Promise<boolean> {
  try {
    return !!(await getOtpConfig()).whatsapp_primary;
  } catch {
    return false;
  }
}

function loadScript(cfg: OtpConfig): Promise<void> {
  scriptPromise ??= new Promise<void>((resolve, reject) => {
    // Primary + fallback hosts, per MSG91's own embed snippet.
    const urls = ["https://verify.msg91.com/otp-provider.js", "https://verify.phone91.com/otp-provider.js"];
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
      script.onerror = () => {
        script.remove();
        if (i + 1 < urls.length) attempt(i + 1);
        else reject(new Error("widget script failed"));
      };
      document.body.appendChild(script);
    };
    attempt(0);
  });
  return scriptPromise;
}

/** Loads the widget script once. Resolves false when the widget isn't
 * configured, is blocked, or is too slow — the caller then uses the
 * classic backend OTP. */
export function ensureOtpWidget(): Promise<boolean> {
  loader ??= (async () => {
    let cfg: OtpConfig;
    try {
      cfg = await getOtpConfig();
    } catch {
      loader = null;
      return false;
    }
    if (!cfg.enabled || !cfg.widget_id || !cfg.token_auth) return false;
    try {
      await withTimeout(loadScript(cfg), SCRIPT_TIMEOUT_MS, "widget script timed out");
      // initSendOTP registers the window methods asynchronously.
      for (let i = 0; i < 40 && !window.sendOtp; i++) await sleep(125);
      return !!window.sendOtp;
    } catch {
      return false; // blocked, failed or too slow — the backend channel takes over
    }
  })();
  return loader;
}

function rawWidgetMessage(e: unknown): string {
  if (typeof e === "string") return e;
  const anyE = e as { message?: unknown } | undefined;
  return typeof anyE?.message === "string" ? anyE.message : "";
}

/** The message to show for any OTP-flow failure: the server's own text,
 * a thrown Error's message, or a plain network hint — never a bare
 * "Something went wrong" that hides what happened. */
export function otpErrorMessage(err: unknown, fallback = "Something went wrong — please try again."): string {
  if (axios.isAxiosError(err)) {
    if (!err.response) return "Can't reach Blussit right now — check your internet and try again.";
    return getErrorMessage(err);
  }
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

/** Sends the OTP to a 10-digit Indian number via the widget. */
export function widgetSendOtp(phone10: string): Promise<void> {
  const send = new Promise<void>((resolve, reject) => {
    if (!window.sendOtp) return reject(new Error("OTP service unavailable"));
    window.sendOtp(
      `91${phone10}`,
      () => resolve(),
      (e) => reject(new Error(rawWidgetMessage(e) || "Couldn't send the code by SMS — please try again.")),
    );
  });
  return withTimeout(send, SEND_TIMEOUT_MS, "Couldn't send the code by SMS — please try again.");
}

/** Verifies the typed OTP; resolves with the MSG91 access token, which the
 * caller must submit to the backend for the real (server-side) check. */
export function widgetVerifyOtp(otp: string): Promise<string> {
  const verify = new Promise<string>((resolve, reject) => {
    if (!window.verifyOtp) return reject(new Error("OTP service unavailable — tap Resend."));
    window.verifyOtp(
      otp,
      (data) => {
        const d = data as { message?: string } | string;
        const token = typeof d === "string" ? d : d?.message;
        if (token && String(token).length > 10) resolve(String(token));
        else reject(new Error("Invalid or expired code."));
      },
      (e) => {
        const raw = rawWidgetMessage(e);
        if (/expire/i.test(raw)) reject(new Error("This code has expired — tap Resend for a new one."));
        else if (!raw || /match|invalid|incorrect|wrong/i.test(raw)) reject(new Error("Invalid or expired code."));
        else reject(new Error(raw));
      },
    );
  });
  return withTimeout(verify, VERIFY_TIMEOUT_MS, "Couldn't check the code — please try again.");
}

export function widgetRetryOtp(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!window.retryOtp) return reject(new Error("OTP service unavailable"));
    window.retryOtp(null, () => resolve(), () => reject(new Error("Couldn't resend — try again in a minute.")));
  });
}

// Server refusals after which the other channel must NOT be tried — it
// would dodge a limit or can't help (no account, staff number, hourly cap).
const FINAL_SEND_ERROR = /no account|staff account|suspended|too many codes/i;

export interface OtpSent {
  channel: OtpChannel;
  /** Seconds until Resend may be tapped — the server's own cooldown. */
  cooldown: number;
}

/**
 * Sends one code, trying channels in order and falling back on failure:
 * default order is backend first once WhatsApp has an approved template
 * (whatsapp_primary), the MSG91 widget (SMS) first until then. A "Please
 * wait Ns" from the backend means its code went out moments ago, so the
 * person just enters that one.
 */
export async function sendOtpCode(phone10: string, backendSend: () => Promise<unknown>, order?: OtpChannel[]): Promise<OtpSent> {
  const channels: OtpChannel[] = order ?? ((await whatsappIsPrimary()) ? ["backend", "widget"] : ["widget", "backend"]);
  let backendError = "";
  let widgetError = "";
  for (const channel of channels) {
    if (channel === "widget") {
      if (!(await ensureOtpWidget())) continue;
      try {
        await widgetSendOtp(phone10);
        return { channel, cooldown: OTP_RESEND_SECONDS };
      } catch (err) {
        widgetError = otpErrorMessage(err);
      }
    } else {
      try {
        await backendSend();
        return { channel, cooldown: OTP_RESEND_SECONDS };
      } catch (err) {
        const message = otpErrorMessage(err);
        const wait = /^Please wait (\d+)s/.exec(message);
        if (wait) return { channel, cooldown: Number(wait[1]) };
        if (FINAL_SEND_ERROR.test(message)) throw new Error(message);
        backendError = message;
      }
    }
  }
  // Backend's generic "couldn't send" says less than the widget's reason.
  const specificBackend = /couldn't send/i.test(backendError) ? "" : backendError;
  throw new Error(specificBackend || widgetError || backendError || "Couldn't send the code right now — please try again in a moment.");
}
