import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MessageCircle, ShieldCheck } from "lucide-react";
import { authApi } from "../../api/auth";
import { Button, Input, Modal } from "../ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";

/**
 * The one-time phone-verification gate a customer's FIRST self-service
 * booking or subscription purchase blocks on (backend: 403
 * PHONE_NOT_VERIFIED — see PhoneNotVerifiedException). Once verified here,
 * phone_verified is permanent on their account, so this never shows again
 * for any booking/subscription after the first.
 *
 * Usage: catch getErrorCode(err) === "PHONE_NOT_VERIFIED" in the calling
 * mutation's onError, open this modal, and retry the original action from
 * onVerified — this modal only handles the OTP round trip, never the
 * underlying booking/subscription submission itself.
 */
export function PhoneVerificationModal({ open, onClose, onVerified }: { open: boolean; onClose: () => void; onVerified: () => void }) {
  const { user, refreshUser } = useAuth();
  const [step, setStep] = useState<"send" | "verify">("send");
  const [otp, setOtp] = useState("");
  const [error, setError] = useState("");

  const sendMutation = useMutation({
    mutationFn: () => authApi.requestPhoneVerification(),
    onSuccess: () => {
      setStep("verify");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const verifyMutation = useMutation({
    mutationFn: () => authApi.confirmPhoneVerification(otp.trim()),
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

  return (
    <Modal open={open} onClose={close} title="Verify your phone number">
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-primary)]" />
          <p className="text-sm text-[var(--color-text-primary)]">
            For your first booking, we need to verify <span className="font-medium">{user?.phone || "your phone number"}</span> with a one-time
            code. You won't need to do this again.
          </p>
        </div>

        {step === "send" ? (
          <>
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" isLoading={sendMutation.isPending} onClick={() => sendMutation.mutate()}>
              <MessageCircle className="h-4 w-4" /> Send code via WhatsApp
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Enter the 6-digit code we sent to your WhatsApp.{" "}
              <button type="button" className="font-medium text-[var(--color-primary)] hover:underline" onClick={() => sendMutation.mutate()} disabled={sendMutation.isPending}>
                Resend
              </button>
            </p>
            <Input label="Verification code" value={otp} onChange={(e) => setOtp(e.target.value)} maxLength={6} autoFocus />
            {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
            <Button className="w-full" disabled={otp.trim().length < 4} isLoading={verifyMutation.isPending} onClick={() => verifyMutation.mutate()}>
              Verify &amp; continue
            </Button>
          </>
        )}
      </div>
    </Modal>
  );
}
