import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Check, Copy, KeyRound, RefreshCw } from "lucide-react";
import { authApi } from "../../api/auth";
import { getErrorMessage } from "../../lib/api-client";
import { Button, Input, Modal } from "../ui";

/** No look-alikes (0/O, 1/l/I) — it gets read out over the phone. */
const ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";
function generatePassword(length = 10): string {
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

/**
 * Staff password recovery (admin: any staff member; manager: own center's
 * captains). A staff account can't reset itself by phone code until its
 * owner verified the phone, so this is the way back in. The temporary
 * password is shown ONCE, to hand over privately — the staff member must
 * replace it at their next login.
 */
export function StaffResetPasswordDialog({ staff, onClose }: { staff: { id: string; full_name: string; role?: string } | null; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [setTo, setSetTo] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setPassword("");
    setError("");
    setSetTo(null);
    setCopied(false);
  }, [staff?.id]);

  const valid = password.length >= 8 && password.length <= 72;
  const reset = useMutation({
    mutationFn: () => authApi.resetStaffPassword(staff!.id, password),
    onSuccess: () => {
      setSetTo(password);
      setPassword("");
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const copy = async () => {
    if (!setTo) return;
    try {
      await navigator.clipboard.writeText(setTo);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard blocked — the password is on screen to copy by hand
    }
  };

  return (
    <Modal open={!!staff} onClose={onClose} title="Reset Password" maxWidth="max-w-sm">
      {staff && setTo ? (
        <div className="space-y-4" data-testid="staff-reset-done">
          <p className="text-sm text-[var(--color-text-secondary)]">
            <span className="font-semibold text-[#0E1A33]">{staff.full_name}</span>&apos;s temporary password — shown only now. Hand it over privately; they must change it when they log in.
          </p>
          <div className="flex items-center justify-between gap-2 rounded-xl border border-[#E4E9F1] bg-[#F4F8FF] px-3.5 py-3">
            <span className="min-w-0 break-all font-mono text-lg font-bold tracking-wide text-[#0E1A33]" data-testid="staff-temp-password">
              {setTo}
            </span>
            <Button size="sm" variant="outline" className="shrink-0" onClick={() => void copy()}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />} {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <Button className="w-full" onClick={onClose}>
            Done
          </Button>
        </div>
      ) : staff ? (
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            Set a temporary password for <span className="font-semibold text-[#0E1A33]">{staff.full_name}</span>. Their other sessions are signed out and they must choose a new password at the next login.
          </p>
          <div className="flex items-end gap-2">
            <div className="min-w-0 flex-1">
              <Input
                label="Temporary Password"
                value={password}
                autoComplete="off"
                maxLength={72}
                onChange={(e) => setPassword(e.target.value)}
                error={password && !valid ? "At least 8 characters." : undefined}
              />
            </div>
            <Button variant="outline" className="shrink-0" onClick={() => setPassword(generatePassword())}>
              <RefreshCw className="h-4 w-4" /> Generate
            </Button>
          </div>
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={onClose}>
              Back
            </Button>
            <Button className="flex-1" isLoading={reset.isPending} disabled={!valid} onClick={() => reset.mutate()}>
              <KeyRound className="h-4 w-4" /> Reset
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
