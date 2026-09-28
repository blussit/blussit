import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MessageCircle, ShieldCheck } from "lucide-react";
import { authApi, googleAuthApi, otpWidgetApi } from "../../api/auth";
import { Button, Input, Modal, OtpInput } from "../ui";
import { useAuth } from "../../context/AuthContext";
import { ensureOtpWidget, otpErrorMessage, sendOtpCode, widgetVerifyOtp, type OtpChannel } from "../../lib/otpWidget";
import { cleanMobileInput, validateIndianMobile } from "../../lib/validators";

/**
 * Phone verification for a logged-in customer: the 90-day re-verification
 * gate (MandatoryGates) and, for a Google sign-in with no phone yet, adding
 * and verifying the primary contact number (add-phone flow). `changeNumber`
 * runs the same add-phone flow for someone who already has a number and
 * wants a new one (manager/admin profile page).
 *
 * Delivery goes through sendOtpCode — the same channel choice and fallback
 * as login and the booking popup (MSG91 widget SMS until an approved
 * WhatsApp template exists, then WhatsApp first). The verify step checks
 * the code on whichever channel actually delivered it: a widget code and a
 * backend code are verified completely differently.
 */
export function PhoneVerificationModal({
  open,
  onClose,
  onVerified,
  autoSend = false,
  changeNumber = false,
}: {
  open: boolean;
  onClose: () => void;
  onVerified: () => void;
  autoSend?: boolean;
  changeNumber?: boolean;
}) {
  const { user, refreshUser } = useAuth();
  const [step, setStep] = useState<"send" | "verify">("send");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  const needsPhone = changeNumber || !user?.phone;
  const [newPhone, setNewPhone] = useState("");
  const targetPhone = (needsPhone ? validateIndianMobile(newPhone) : user?.phone) || "";
  const sameAsCurrent = changeNumber && !!targetPhone && targetPhone === user?.phone;
  const [channel, setChannel] = useState<OtpChannel | null>(null);
  const [delivered, setDelivered] = useState<"whatsapp" | "sms" | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [smsAvailable, setSmsAvailable] = useState(false);
  const verifyingRef = useRef(false);

  const sendMutation = useMutation({
    mutationFn: async (order?: OtpChannel[]) => {
      const phone = needsPhone ? validateIndianMobile(newPhone) : user?.phone;
      if (!phone) throw new Error("Enter a valid 10-digit mobile number.");
      let backendChannel: "whatsapp" | "sms" | undefined;
      const sent = await sendOtpCode(
        phone,
        async () => {
          const res = needsPhone ? await googleAuthApi.addPhoneRequest(phone) : await authApi.requestPhoneVerification();
          backendChannel = res.channel;
        },
        order,
      );
      return { ...sent, delivered: sent.channel === "widget" ? ("sms" as const) : (backendChannel ?? null) };
    },
    onSuccess: (sent) => {
      setChannel(sent.channel);
      setDelivered(sent.delivered);
      setCooldown(sent.cooldown);
      setOtp("");
      setStep("verify");
      setError("");
    },
    onError: (err) => {
      setError(otpErrorMessage(err));
      setCooldown(0);
    },
  });

  const verifyMutation = useMutation({
    // The code comes in as the mutation variable — reading `otp` state here
    // would see the value from before a paste/autofill landed.
    mutationFn: async (code: string) => {
      if (needsPhone) {
        const payload = channel === "widget" ? { phone: targetPhone, access_token: await widgetVerifyOtp(code) } : { phone: targetPhone, otp: code };
        await googleAuthApi.addPhoneConfirm(payload);
        return;
      }
      if (channel === "widget") {
        await otpWidgetApi.verifyPhone(await widgetVerifyOtp(code));
        return;
      }
      await authApi.confirmPhoneVerification(code);
    },
    onSuccess: async () => {
      await refreshUser();
      setOtp("");
      setStep("send");
      setError("");
      onVerified();
    },
    onError: (err) => {
      setError(otpErrorMessage(err));
      setOtp("");
    },
  });

  const submitCode = (code: string) => {
    const clean = code.trim();
    if (clean.length < 6 || verifyingRef.current) return;
    verifyingRef.current = true;
    setError("");
    verifyMutation.mutate(clean, { onSettled: () => (verifyingRef.current = false) });
  };

  const send = (order?: OtpChannel[]) => {
    if (!sendMutation.isPending) sendMutation.mutate(order);
  };

  const autoSent = useRef(false);
  useEffect(() => {
    if (!open) {
      autoSent.current = false;
      return;
    }
    if (!autoSend || autoSent.current || step !== "send" || needsPhone) return;
    autoSent.current = true;
    sendMutation.mutate(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSend, open, step, needsPhone]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  useEffect(() => {
    if (open && channel === "backend") void ensureOtpWidget().then(setSmsAvailable);
  }, [open, channel]);

  const close = () => {
    setStep("send");
    setOtp("");
    setError("");
    setChannel(null);
    setDelivered(null);
    setNewPhone("");
    autoSent.current = false;
    onClose();
  };

  const sentVia = delivered === "sms" ? " by SMS" : delivered === "whatsapp" ? " on WhatsApp" : "";
  const resendOrder: OtpChannel[] | undefined = channel ? [channel, channel === "widget" ? "backend" : "widget"] : undefined;

  return (
    <Modal open={open} onClose={close} title={changeNumber ? "Change Phone Number" : "Verify Your Phone Number"}>
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-primary)]" />
          <p className="text-sm text-[var(--color-text-primary)]">
            {changeNumber
              ? "Enter your new mobile number — we'll send a one-time code to it to confirm it's yours."
              : needsPhone
              ? "Your phone number is our primary contact for bookings — add it once and verify with a one-time code."
              : step === "verify"
                ? <>We sent an OTP{sentVia} to <span className="font-medium">{user?.phone}</span>.</>
                : <>We'll send an OTP to <span className="font-medium">{user?.phone}</span> to verify it's you.</>}
          </p>
        </div>

        {step === "send" ? (
          <>
            {autoSend && sendMutation.isPending && (
              <p className="text-sm text-[var(--color-text-secondary)]">Sending Your Verification Code...</p>
            )}
            {needsPhone && (
              <Input
                label={changeNumber ? "New Mobile Number" : "Mobile Number"}
                type="tel"
                inputMode="numeric"
                autoComplete="tel"
                value={newPhone}
                onChange={(e) => setNewPhone(cleanMobileInput(e.target.value))}
                placeholder="10-Digit Mobile"
                autoFocus
              />
            )}
            {sameAsCurrent && <p className="text-sm text-[var(--color-text-secondary)]">This is already your number.</p>}
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={(needsPhone && !validateIndianMobile(newPhone)) || sameAsCurrent} isLoading={sendMutation.isPending} onClick={() => send()}>
              <MessageCircle className="h-4 w-4" /> Send Code
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Enter the 6-digit code sent to <span className="font-medium text-black">+91 {targetPhone}</span>.
            </p>
            <OtpInput value={otp} onChange={setOtp} autoFocus disabled={verifyMutation.isPending} onComplete={submitCode} />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={otp.trim().length < 6 || sendMutation.isPending} isLoading={verifyMutation.isPending} onClick={() => submitCode(otp)}>
              Verify And Continue
            </Button>
            <div className="flex items-center justify-between text-xs text-[var(--color-text-secondary)]">
              {needsPhone ? (
                <button type="button" className="font-semibold text-black hover:underline" onClick={() => { setStep("send"); setError(""); setOtp(""); }}>
                  Change number
                </button>
              ) : (
                <span />
              )}
              {cooldown > 0 ? (
                <span>Resend in {cooldown}s</span>
              ) : (
                <span className="flex items-center gap-3">
                  {channel === "backend" && smsAvailable && (
                    <button type="button" className="font-semibold text-black hover:underline disabled:opacity-50" disabled={sendMutation.isPending} onClick={() => send(["widget"])}>
                      Get it by SMS
                    </button>
                  )}
                  <button type="button" className="font-semibold text-black hover:underline disabled:opacity-50" disabled={sendMutation.isPending} onClick={() => send(resendOrder)}>
                    Resend code
                  </button>
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
