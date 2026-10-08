/**
 * Customer settings: name, the "WhatsApp reminders & offers" switch
 * (marketing_opt_out — booking updates always go out), saved addresses,
 * password, and the policies.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FileText, KeyRound, MapPin, MessageCircle, ShieldCheck } from "lucide-react";
import { addressApi, userApi } from "../../api/profile";
import { authApi } from "../../api/auth";
import { Input } from "../../components/ui";
import { btn, card, MenuRow, PageHeader } from "../../components/customer/ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";
import { cn } from "../../lib/cn";

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h2 className="px-1 text-[13px] font-semibold uppercase tracking-wide text-[#8A94A6]">{title}</h2>
      <div className={`${card} divide-y divide-[#EEF2F7] overflow-hidden`}>{children}</div>
    </section>
  );
}

export default function SettingsPage() {
  const { user, refreshUser } = useAuth();
  const { data: addresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list });

  const [fullName, setFullName] = useState(user?.full_name || "");
  const [nameMsg, setNameMsg] = useState("");
  const [nameError, setNameError] = useState("");
  const saveName = useMutation({
    mutationFn: () => userApi.updateProfile({ full_name: fullName.trim() }),
    onSuccess: async () => {
      setNameError("");
      setNameMsg("Saved.");
      await refreshUser();
    },
    onError: (err) => {
      setNameMsg("");
      setNameError(getErrorMessage(err));
    },
  });

  // WhatsApp reminders & offers: on = not opted out. Flipped at once; the
  // saved account is the truth once the request answers.
  const [optInDraft, setOptInDraft] = useState<boolean | null>(null);
  const [marketingError, setMarketingError] = useState("");
  const marketing = useMutation({
    mutationFn: (optIn: boolean) => userApi.updateProfile({ marketing_opt_out: !optIn }),
    onMutate: (optIn) => {
      setMarketingError("");
      setOptInDraft(optIn);
    },
    onSuccess: async (updated, optIn) => {
      const fresh = await refreshUser();
      const savedOptOut = fresh?.marketing_opt_out ?? updated?.marketing_opt_out ?? false;
      if (savedOptOut !== !optIn) setMarketingError("Couldn't save that right now. Please try again later.");
      setOptInDraft(null);
    },
    onError: (err) => {
      setOptInDraft(null);
      setMarketingError(getErrorMessage(err));
    },
  });
  const optedIn = optInDraft ?? !user?.marketing_opt_out;

  const [changingPassword, setChangingPassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordMsg, setPasswordMsg] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const changePassword = useMutation({
    mutationFn: () => authApi.changePassword({ current_password: currentPassword, new_password: newPassword }),
    onSuccess: async () => {
      setPasswordMsg("Password updated.");
      setPasswordError("");
      setCurrentPassword("");
      setNewPassword("");
      setChangingPassword(false);
      await refreshUser();
    },
    onError: (err) => setPasswordError(getErrorMessage(err)),
  });

  const nameChanged = fullName.trim().length > 1 && fullName.trim() !== (user?.full_name || "");

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader back="/app/profile" title="Settings" />

      <Group title="Your Details">
        <form
          className="space-y-3 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (nameChanged) saveName.mutate();
          }}
        >
          <Input
            label="Full Name"
            value={fullName}
            onChange={(e) => {
              setNameMsg("");
              setFullName(e.target.value);
            }}
          />
          <p className="text-xs text-[#5F6878]">
            Phone <span className="tabular-nums text-[#0E1A33]">{user?.phone || "—"}</span> — it's how you sign in, so it can't be changed here.
          </p>
          {nameError && <p className="text-sm text-[#C62828]">{nameError}</p>}
          {nameMsg && <p className="text-sm text-[#1E7B3C]">{nameMsg}</p>}
          <button type="submit" className={btn("primary", "sm")} disabled={!nameChanged || saveName.isPending}>
            {saveName.isPending ? "Saving…" : "Save Name"}
          </button>
        </form>
      </Group>

      <Group title="Notifications">
        <button
          type="button"
          role="switch"
          aria-checked={optedIn}
          disabled={marketing.isPending}
          onClick={() => marketing.mutate(!optedIn)}
          className="flex w-full items-center gap-3.5 px-4 py-3.5 text-left transition-colors hover:bg-[#F7F9FC] disabled:cursor-wait"
        >
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0E1A33]">
            <MessageCircle className="h-[18px] w-[18px]" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium text-[#0E1A33]">WhatsApp Reminders & Offers</span>
            <span className="block text-xs text-[#5F6878]">Booking updates always come.</span>
          </span>
          <span aria-hidden className={cn("relative h-6 w-11 shrink-0 rounded-full transition-colors", optedIn ? "bg-[#0A66F0]" : "bg-[#CBD3DF]")}>
            <span className={cn("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform", optedIn ? "translate-x-[22px]" : "translate-x-0.5")} />
          </span>
        </button>
      </Group>
      {marketingError && <p className="-mt-3 px-1 text-sm text-[#C62828]">{marketingError}</p>}

      <Group title="Addresses">
        <MenuRow icon={MapPin} label="Saved Addresses" meta={addresses?.length ? String(addresses.length) : undefined} to="/app/addresses" />
      </Group>

      <Group title="Security">
        <MenuRow icon={KeyRound} label="Change Password" onClick={() => setChangingPassword((v) => !v)} />
        {changingPassword && (
          <div className="space-y-3 p-4">
            <Input label="Current Password" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
            <Input label="New Password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} hint="At least 8 characters" />
            {passwordError && <p className="text-sm text-[#C62828]">{passwordError}</p>}
            <button type="button" className={btn("primary", "sm")} disabled={changePassword.isPending} onClick={() => changePassword.mutate()}>
              {changePassword.isPending ? "Updating…" : "Update Password"}
            </button>
          </div>
        )}
      </Group>
      {passwordMsg && <p className="-mt-3 px-1 text-sm text-[#1E7B3C]">{passwordMsg}</p>}

      <Group title="Policies">
        <MenuRow icon={FileText} label="Cancellation Policy" to="/cancellation-policy" />
        <MenuRow icon={ShieldCheck} label="Privacy Policy" to="/privacy-policy" />
      </Group>
    </div>
  );
}
