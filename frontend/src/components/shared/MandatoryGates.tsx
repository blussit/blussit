/**
 * Blocking account gates, rendered inside every logged-in shell:
 *
 * 1. must_change_password — the account runs on a temp/unknown password
 *    (manager reset, WhatsApp auto-created, abandoned guest signup).
 *    A non-dismissible modal forces a real password before anything else;
 *    closing the tab just brings it back next visit.
 * 2. 90-day phone re-verification (customers) — phone_verification_stale
 *    from the backend re-runs the OTP gate on login.
 *
 * Password comes first: it's the stronger claim on the account.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { guestAuthApi } from "../../api/auth";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { roleHomePath } from "../../lib/roleHome";
import { Button, Input, Modal } from "../ui";
import { PhoneVerificationModal } from "./PhoneVerificationModal";

export function MandatoryGates() {
  const { user, refreshUser, logout } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: () => guestAuthApi.setPassword(password),
    onSuccess: async () => {
      setError("");
      await refreshUser();
      // A fresh password means a fresh start — land on the dashboard,
      // wherever the gate happened to catch them.
      navigate(roleHomePath(user?.role), { replace: true });
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (!user) return null;

  if (user.must_change_password) {
    const valid = password.length >= 8 && password === confirm;
    return (
      <Modal open onClose={() => undefined} title="Set your password">
        <div className="space-y-4">
          <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
            <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-primary)]" />
            <p className="text-sm text-[var(--color-text-primary)]">
              Your account is using a temporary password. Choose your own password to continue — you'll use it to log in from now on.
            </p>
          </div>
          <Input label="New password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} hint="At least 8 characters" autoFocus />
          <Input
            label="Confirm password"
            type="password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={confirm && password !== confirm ? "Passwords don't match" : undefined}
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button className="w-full" disabled={!valid} isLoading={save.isPending} onClick={() => save.mutate()}>
            Save password &amp; continue
          </Button>
          {/* The one escape hatch — this modal is deliberately
              non-dismissible, but "non-dismissible with no way out" meant
              a failing save trapped people until they cleared storage. */}
          <button
            type="button"
            onClick={logout}
            className="w-full text-center text-xs font-medium text-[var(--color-text-secondary)] underline hover:text-[var(--color-text-primary)]"
          >
            Log out instead
          </button>
        </div>
      </Modal>
    );
  }

  if (user.role === "customer" && user.phone_verification_stale && user.phone) {
    // Non-dismissible: onClose is a no-op; verification itself closes it
    // by refreshing the user (stale flag flips off).
    return <PhoneVerificationModal open onClose={() => undefined} onVerified={() => undefined} />;
  }

  return null;
}
