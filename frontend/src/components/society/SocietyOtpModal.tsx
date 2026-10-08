import { useEffect, useRef, useState } from "react";
import { bookingApi } from "../../api/booking";
import { Modal, OtpInput } from "../ui";
import { ensureOtpWidget, otpErrorMessage, sendOtpCode, widgetVerifyOtp, type OtpChannel } from "../../lib/otpWidget";

export type SocietyPhoneProof = { phone_otp?: string; phone_access_token?: string };

/**
 * Phone check for the society form — same mechanics as the booking OTP
 * (WhatsApp/backend code or the MSG91 widget), society wording. The code is
 * proven server-side when the request is submitted.
 */
export function SocietyOtpModal({
  open,
  phone,
  busy,
  error: outerError,
  onClose,
  onVerified,
}: {
  open: boolean;
  phone: string;
  busy?: boolean;
  error?: string;
  onClose: () => void;
  onVerified: (proof: SocietyPhoneProof) => void;
}) {
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [channel, setChannel] = useState<OtpChannel | null>(null);
  const [verifying, setVerifying] = useState(false);
  // Refs, not state: a pasted code fires onComplete and a tap can land in
  // the same tick, before a state update would disable anything — without
  // this a double verify meant two enrol requests.
  const sendingRef = useRef(false);
  const verifyingRef = useRef(false);
  const sentFor = useRef<string | null>(null);

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
      sentFor.current = phone;
    } catch (err) {
      setError(otpErrorMessage(err));
      setCooldown(0);
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    if (sentFor.current === phone && cooldown > 0) return;
    void send();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, phone]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (open && channel === "backend") void ensureOtpWidget();
  }, [open, channel]);

  useEffect(() => {
    if (outerError) setOtp("");
  }, [outerError]);

  const verify = async (code: string) => {
    const clean = code.trim();
    if (clean.length < 6 || busy || verifyingRef.current || sendingRef.current) return;
    verifyingRef.current = true;
    setVerifying(true);
    setError("");
    try {
      onVerified(channel === "widget" ? { phone_access_token: await widgetVerifyOtp(clean) } : { phone_otp: clean });
    } catch (err) {
      setError(otpErrorMessage(err));
      setOtp("");
    } finally {
      verifyingRef.current = false;
      setVerifying(false);
    }
  };

  const shown = error || outerError;
  return (
    <Modal open={open} onClose={onClose} title="Verify Your Number" maxWidth="max-w-sm">
      <div className="space-y-4">
        <p className="text-sm text-[#5F6878]">
          {sending ? "Sending a code to " : "Enter the 6-digit code sent to "}
          <span className="font-semibold text-[#0E1A33]">+91 {phone}</span>
        </p>
        <OtpInput value={otp} onChange={setOtp} autoFocus disabled={busy || verifying} onComplete={(code) => void verify(code)} />
        {shown && <p className="text-sm text-[var(--color-error)]">{shown}</p>}
        <button
          type="button"
          disabled={otp.length < 6 || sending || busy || verifying}
          onClick={() => void verify(otp)}
          className="flex h-12 w-full items-center justify-center rounded-[14px] bg-[#0A66F0] text-sm font-bold text-white transition hover:bg-[#0857CC] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Saving…" : verifying ? "Verifying…" : "Verify And Continue"}
        </button>
        <div className="flex items-center justify-between text-xs text-[#5F6878]">
          <button type="button" className="font-semibold text-[#0E1A33] hover:underline" onClick={onClose}>
            Edit Number
          </button>
          {cooldown > 0 ? (
            <span>Resend In {cooldown}s</span>
          ) : (
            <button type="button" className="font-semibold text-[#0A66F0] hover:underline disabled:opacity-50" disabled={sending} onClick={() => void send()}>
              Resend Code
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
