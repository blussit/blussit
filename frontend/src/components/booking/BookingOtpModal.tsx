import { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { bookingApi, type PhoneProof } from "../../api/booking";
import { Button, Modal, OtpInput } from "../ui";
import { getErrorMessage } from "../../lib/api-client";
import { ensureOtpWidget, whatsappIsPrimary, widgetSendOtp, widgetVerifyOtp } from "../../lib/otpWidget";

const RESEND_SECONDS = 30;

/**
 * The last step of an anonymous website booking — the same OTP mechanics as
 * login: MSG91 (SMS, via the widget) leads until an approved WhatsApp OTP
 * template exists, after which WhatsApp leads and MSG91 is the fallback.
 * The code is proven server-side when the booking is submitted.
 * "Edit number" backs out to the phone field.
 */
export function BookingOtpModal({
  open,
  phone,
  initialError = "",
  onClose,
  onEditNumber,
  onVerified,
}: {
  open: boolean;
  phone: string;
  /** Set when reopened because the last code was rejected — skips the auto-send. */
  initialError?: string;
  onClose: () => void;
  onEditNumber: () => void;
  onVerified: (proof: PhoneProof) => void;
}) {
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [viaWidget, setViaWidget] = useState(false);
  const sentFor = useRef<string | null>(null);

  const send = async () => {
    setSending(true);
    setError("");
    setOtp("");
    const order = (await whatsappIsPrimary()) ? (["backend", "widget"] as const) : (["widget", "backend"] as const);
    let lastError = "";
    for (const channel of order) {
      try {
        if (channel === "widget") {
          if (!(await ensureOtpWidget())) continue;
          await widgetSendOtp(phone);
          setViaWidget(true);
        } else {
          await bookingApi.requestPhoneOtp(phone);
          setViaWidget(false);
        }
        setCooldown(RESEND_SECONDS);
        setSending(false);
        return;
      } catch (err) {
        const message = getErrorMessage(err);
        // A backend code went out moments ago (modal reopened) — just enter it.
        if (channel === "backend" && message.startsWith("Please wait")) {
          setViaWidget(false);
          setCooldown(parseInt(message.replace(/\D/g, ""), 10) || RESEND_SECONDS);
          setSending(false);
          return;
        }
        lastError = message;
      }
    }
    setError(lastError || "Couldn't send the code right now — please try again in a moment.");
    setSending(false);
  };

  useEffect(() => {
    if (!open) {
      sentFor.current = null;
      return;
    }
    if (initialError) {
      setError(initialError);
      setOtp("");
      return;
    }
    if (sentFor.current === phone) return;
    sentFor.current = phone;
    void send();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phone, initialError]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const verify = async (code: string) => {
    if (code.length < 6 || verifying) return;
    setVerifying(true);
    setError("");
    try {
      onVerified(viaWidget ? { phone_access_token: await widgetVerifyOtp(code.trim()) } : { phone_otp: code.trim() });
      setOtp("");
    } catch (err) {
      setError(getErrorMessage(err));
      setOtp("");
    } finally {
      setVerifying(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Confirm Your Booking" maxWidth="max-w-sm">
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-primary)]" />
          <p className="text-sm text-[var(--color-text-primary)]">
            {sending ? "Sending a code to " : `Enter the 6-digit code we sent ${viaWidget ? "by SMS " : ""}to `}
            <span className="font-semibold">+91 {phone}</span>
          </p>
        </div>

        <OtpInput value={otp} onChange={setOtp} autoFocus disabled={verifying} onComplete={verify} />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

        <Button className="w-full" disabled={otp.length < 6 || sending} isLoading={verifying} onClick={() => verify(otp)}>
          Verify And Book
        </Button>

        <div className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
          <button type="button" className="font-semibold text-black hover:underline" onClick={onEditNumber}>
            Edit number
          </button>
          {cooldown > 0 ? (
            <span>Resend in {cooldown}s</span>
          ) : (
            <button type="button" className="font-semibold text-black hover:underline disabled:opacity-50" disabled={sending} onClick={() => void send()}>
              Resend code
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
