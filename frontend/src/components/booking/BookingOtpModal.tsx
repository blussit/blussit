import { useEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { Button, Modal, OtpInput } from "../ui";
import { getErrorMessage } from "../../lib/api-client";

const RESEND_SECONDS = 30;

/**
 * The last step of an anonymous website booking: a code goes to the typed
 * number the moment this opens (WhatsApp first, instant SMS fallback — the
 * backend decides), and the booking only goes through once it's entered.
 * "Edit number" backs out to the phone field.
 */
export function BookingOtpModal({
  open,
  phone,
  onClose,
  onEditNumber,
  onVerified,
}: {
  open: boolean;
  phone: string;
  onClose: () => void;
  onEditNumber: () => void;
  onVerified: (token: string) => void;
}) {
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const sentFor = useRef<string | null>(null);

  const send = async () => {
    setSending(true);
    setError("");
    setOtp("");
    try {
      await bookingApi.requestPhoneOtp(phone);
      setCooldown(RESEND_SECONDS);
    } catch (err) {
      const message = getErrorMessage(err);
      // A code went out moments ago (modal reopened) — just let them enter it.
      if (message.startsWith("Please wait")) setCooldown(parseInt(message.replace(/\D/g, ""), 10) || RESEND_SECONDS);
      else setError(message);
    } finally {
      setSending(false);
    }
  };

  useEffect(() => {
    if (!open) {
      sentFor.current = null;
      return;
    }
    if (sentFor.current === phone) return;
    sentFor.current = phone;
    void send();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phone]);

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
      const token = await bookingApi.confirmPhoneOtp(phone, code);
      setOtp("");
      onVerified(token);
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
            {sending ? "Sending a code to " : "Enter the 6-digit code we sent to "}
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
