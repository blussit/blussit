import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { Button } from "../ui";
import { PhoneVerificationModal } from "./PhoneVerificationModal";

/**
 * Staff (admin / manager / captain): until they prove the phone on file is
 * theirs with a code, "Forgot password" can't reset a staff account — only
 * an admin (or, for a captain, their manager) can. One tap verifies it.
 * Renders nothing once verified, or for an account with no phone.
 */
export function StaffPhoneVerify({ className = "", onVerified }: { className?: string; onVerified?: () => void }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  if (!user || user.role === "customer" || !user.phone) return null;
  if (done || user.phone_self_verified !== false) return null;
  return (
    <div className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#F5D58A] bg-[#FFF8E6] px-3.5 py-3 ${className}`} data-testid="staff-verify-phone">
      <p className="min-w-0 flex-1 text-sm text-[#0E1A33]">
        <span className="font-semibold">Verify Your Phone</span>
        <span className="block text-xs text-[#5F6878]">So you can reset your own password with a code if you ever forget it.</span>
      </p>
      <Button size="sm" variant="info" className="shrink-0" onClick={() => setOpen(true)}>
        <ShieldCheck className="h-4 w-4" /> Verify Phone
      </Button>
      <PhoneVerificationModal
        open={open}
        autoSend
        onClose={() => setOpen(false)}
        onVerified={() => {
          setOpen(false);
          setDone(true);
          onVerified?.();
        }}
      />
    </div>
  );
}
