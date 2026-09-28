import { useEffect, useRef, useState } from "react";
import { bookingApi, type PhoneProof } from "../../api/booking";
import { Button, Modal, OtpInput } from "../ui";
import { ensureOtpWidget, otpErrorMessage, sendOtpCode, widgetVerifyOtp, type OtpChannel } from "../../lib/otpWidget";

/**
 * The last step of an anonymous website booking — the same OTP mechanics as
 * login: MSG91 (SMS, via the widget) leads until an approved WhatsApp OTP
 * template exists, after which WhatsApp leads and MSG91 is the fallback
 * (sendOtpCode). The code is proven server-side when the booking is
 * submitted. The booking form behind this popup is never touched: closing
 * it, a wrong code or "Edit number" all leave every field as it was.
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
  const [channel, setChannel] = useState<OtpChannel | null>(null);
  const [smsAvailable, setSmsAvailable] = useState(false);
  // Refs, not state: a pasted code fires onComplete and a tap can land in
  // the same tick, before a state update would disable anything.
  const sendingRef = useRef(false);
  const verifyingRef = useRef(false);
  const handledOpen = useRef(false);
  const lastSentPhone = useRef<string | null>(null);

  const send = async (order?: OtpChannel[]) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    setError("");
    setOtp("");
    try {
      const sent = await sendOtpCode(phone, () => bookingApi.requestPhoneOtp(phone), order);
      setChannel(sent.channel);
      setCooldown(sent.cooldown);
      lastSentPhone.current = phone;
    } catch (err) {
      setError(otpErrorMessage(err));
      setCooldown(0); // nothing went out — let them retry now
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  useEffect(() => {
    if (!open) {
      handledOpen.current = false;
      return;
    }
    if (handledOpen.current) return;
    handledOpen.current = true;
    if (initialError) {
      setError(initialError);
      setOtp("");
      return;
    }
    // Closed and reopened moments after a send: that code is still coming.
    if (lastSentPhone.current === phone && cooldown > 0) {
      setError("");
      return;
    }
    void send();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phone, initialError]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (open && channel === "backend") void ensureOtpWidget().then(setSmsAvailable);
  }, [open, channel]);

  const verify = async (code: string) => {
    const clean = code.trim();
    if (clean.length < 6 || verifyingRef.current || sendingRef.current) return;
    verifyingRef.current = true;
    setVerifying(true);
    setError("");
    try {
      const proof: PhoneProof = channel === "widget" ? { phone_access_token: await widgetVerifyOtp(clean) } : { phone_otp: clean };
      setOtp("");
      onVerified(proof);
    } catch (err) {
      setError(otpErrorMessage(err));
      setOtp("");
    } finally {
      verifyingRef.current = false;
      setVerifying(false);
    }
  };

  const resendOrder: OtpChannel[] | undefined = channel ? [channel, channel === "widget" ? "backend" : "widget"] : undefined;

  return (
    <Modal open={open} onClose={onClose} title="Confirm Your Booking" maxWidth="max-w-sm">
      <div className="space-y-4">
        <p className="text-sm text-gray-600">
          {sending ? "Sending a code to " : `Enter the 6-digit code sent ${channel === "widget" ? "by SMS " : ""}to `}
          <span className="font-semibold text-black">+91 {phone}</span>
        </p>

        <OtpInput value={otp} onChange={setOtp} autoFocus disabled={verifying} onComplete={(code) => void verify(code)} />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}

        <Button variant="info" className="w-full font-semibold" disabled={otp.length < 6 || sending} isLoading={verifying} onClick={() => void verify(otp)}>
          Verify And Book
        </Button>

        <div className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
          <button type="button" className="font-semibold text-black hover:underline" onClick={onEditNumber}>
            Edit number
          </button>
          {cooldown > 0 ? (
            <span>Resend in {cooldown}s</span>
          ) : (
            <span className="flex items-center gap-3">
              {channel === "backend" && smsAvailable && (
                <button type="button" className="font-semibold text-black hover:underline disabled:opacity-50" disabled={sending || verifying} onClick={() => void send(["widget"])}>
                  Get it by SMS
                </button>
              )}
              <button type="button" className="font-semibold text-black hover:underline disabled:opacity-50" disabled={sending || verifying} onClick={() => void send(resendOrder)}>
                Resend code
              </button>
            </span>
          )}
        </div>
      </div>
    </Modal>
  );
}
