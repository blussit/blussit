import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Sparkles } from "lucide-react";
import { Button, Input } from "../../components/ui";
import { authApi } from "../../api/auth";
import { getErrorMessage } from "../../lib/api-client";

export default function ForgotPasswordPage() {
  const [step, setStep] = useState<"request" | "reset" | "done">("request");
  const [identifier, setIdentifier] = useState("");
  const [otp, setOtp] = useState("");
  const [debugOtp, setDebugOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const requestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      const result = await authApi.forgotPassword(identifier);
      setDebugOtp(result.debug_otp);
      setStep("reset");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  const resetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      await authApi.resetPassword({ identifier, otp, new_password: newPassword });
      setStep("done");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-surface)] px-4">
      <div className="w-full max-w-sm">
        <Link to="/" className="mb-8 flex items-center justify-center gap-2 font-display text-lg font-bold text-[var(--color-primary)]">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--color-primary)] text-white">
            <Sparkles className="h-5 w-5" />
          </span>
          CLEAN<span className="text-[var(--color-secondary)]">RIDE</span>
        </Link>

        <div className="rounded-2xl border border-gray-100 bg-white p-8 shadow-[var(--shadow-soft)]">
          {step === "request" && (
            <>
              <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Reset your password</h1>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Enter your email or phone to receive a reset code.</p>
              <form className="mt-6 space-y-4" onSubmit={requestOtp}>
                <Input label="Email or phone number" value={identifier} onChange={(e) => setIdentifier(e.target.value)} required />
                {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
                <Button type="submit" className="w-full" isLoading={isLoading}>
                  Send reset code
                </Button>
              </form>
            </>
          )}

          {step === "reset" && (
            <>
              <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">Enter reset code</h1>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                We generated a code for <span className="font-medium">{identifier}</span>.
              </p>
              {debugOtp && (
                <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  Phase 1 preview (no SMS/WhatsApp provider yet) — your code is <strong>{debugOtp}</strong>
                </p>
              )}
              <form className="mt-6 space-y-4" onSubmit={resetPassword}>
                <Input label="Reset code" value={otp} onChange={(e) => setOtp(e.target.value)} required />
                <Input label="New password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required />
                {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
                <Button type="submit" className="w-full" isLoading={isLoading}>
                  Reset password
                </Button>
              </form>
            </>
          )}

          {step === "done" && (
            <div className="text-center">
              <CheckCircle2 className="mx-auto h-10 w-10 text-[var(--color-success)]" />
              <h1 className="mt-3 text-xl font-semibold text-[var(--color-text-primary)]">Password reset</h1>
              <p className="mt-1 text-sm text-[var(--color-text-secondary)]">You can now log in with your new password.</p>
              <Link to="/login">
                <Button className="mt-6 w-full">Back to login</Button>
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
