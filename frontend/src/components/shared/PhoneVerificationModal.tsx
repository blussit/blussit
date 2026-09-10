import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { MessageCircle, ShieldCheck } from "lucide-react";
import { authApi, googleAuthApi, otpWidgetApi } from "../../api/auth";
import { Button, Input, Modal, OtpInput } from "../ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { ensureOtpWidget, widgetSendOtp, widgetVerifyOtp } from "../../lib/otpWidget";
import { validateIndianMobile } from "../../lib/validators";

/**
 * The one-time phone-verification gate a customer's FIRST self-service
 * booking or subscription purchase blocks on (backend: 403
 * PHONE_NOT_VERIFIED — see PhoneNotVerifiedException). Once verified here,
 * phone_verified is permanent on their account, so this never shows again
 * for any booking/subscription after the first.
 *
 * Two delivery paths, decided by the backend's widget config:
 *  - MSG91 OTP widget (preferred once configured): MSG91 sends the code
 *    over SMS/WhatsApp and our backend verifies the resulting token
 *    server-side with identifier binding.
 *  - Classic backend-generated OTP over WhatsApp (fallback).
 */
export function PhoneVerificationModal({ open, onClose, onVerified }: { open: boolean; onClose: () => void; onVerified: () => void }) {
  const { user, refreshUser } = useAuth();
  const [step, setStep] = useState<"send" | "verify">("send");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");
  // Google sign-ins arrive with no phone at all — collect the primary
  // contact number here and verify it in the same breath (add-phone flow).
  const needsPhone = !user?.phone;
  const [newPhone, setNewPhone] = useState("");
  const targetPhone = user?.phone || validateIndianMobile(newPhone) || "";

  const { data: widgetReady } = useQuery({
    queryKey: ["otp-widget-ready"],
    queryFn: ensureOtpWidget,
    enabled: open,
    staleTime: Infinity,
  });

  const sendMutation = useMutation({
    mutationFn: async () => {
      if (needsPhone) {
        const phone = validateIndianMobile(newPhone);
        if (!phone) throw new Error("Enter a valid 10-digit mobile number");
        if (widgetReady) await widgetSendOtp(phone);
        else await googleAuthApi.addPhoneRequest(phone);
        return;
      }
      if (widgetReady && user?.phone) {
        await widgetSendOtp(user.phone);
        return;
      }
      await authApi.requestPhoneVerification();
    },
    onSuccess: () => {
      setStep("verify");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const verifyMutation = useMutation({
    mutationFn: async () => {
      if (needsPhone) {
        const payload = widgetReady
          ? { phone: targetPhone, access_token: await widgetVerifyOtp(otp.trim()) }
          : { phone: targetPhone, otp: otp.trim() };
        await googleAuthApi.addPhoneConfirm(payload);
        return;
      }
      if (widgetReady && user?.phone) {
        const token = await widgetVerifyOtp(otp.trim());
        await otpWidgetApi.verifyPhone(token);
        return;
      }
      await authApi.confirmPhoneVerification(otp.trim());
    },
    onSuccess: async () => {
      await refreshUser();
      setOtp("");
      setStep("send");
      setError("");
      onVerified();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const close = () => {
    setStep("send");
    setOtp("");
    setError("");
    onClose();
  };

  const channelLabel = widgetReady ? "SMS / WhatsApp" : "WhatsApp";

  return (
    <Modal open={open} onClose={close} title="Verify your phone number">
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-primary)]" />
          <p className="text-sm text-[var(--color-text-primary)]">
            {needsPhone
              ? "Your phone number is our primary contact for bookings — add it once and verify with a one-time code."
              : step === "verify"
                ? <>We sent an OTP on <span className="font-medium">{user?.phone}</span>.</>
                : <>We'll send an OTP on <span className="font-medium">{user?.phone}</span> to verify it's you.</>}
          </p>
        </div>

        {step === "send" ? (
          <>
            {needsPhone && (
              <Input
                label="Mobile number"
                value={newPhone}
                onChange={(e) => setNewPhone(e.target.value.replace(/\D/g, "").slice(0, 10))}
                placeholder="10-digit mobile"
                autoFocus
              />
            )}
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={needsPhone && !validateIndianMobile(newPhone)} isLoading={sendMutation.isPending} onClick={() => sendMutation.mutate()}>
              <MessageCircle className="h-4 w-4" /> Send code via {channelLabel}
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">Enter the 6-digit code we sent over {channelLabel}.</p>
            <OtpInput
              value={otp}
              onChange={setOtp}
              autoFocus
              disabled={verifyMutation.isPending}
              // Six digits in = nothing left to decide; submit for them.
              onComplete={() => verifyMutation.mutate()}
            />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={otp.trim().length < 6} isLoading={verifyMutation.isPending} onClick={() => verifyMutation.mutate()}>
              Verify &amp; continue
            </Button>
            <p className="text-center text-xs text-[var(--color-text-secondary)]">
              Didn't get it?{" "}
              <button type="button" className="font-semibold text-black hover:underline" onClick={() => sendMutation.mutate()} disabled={sendMutation.isPending}>
                Resend code
              </button>
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}
