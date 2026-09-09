/**
 * Customer profile — grouped, glanceable rows (the mobile mockup's
 * blueprint): identity card up top, then Account / Security / More
 * groups. Name editing and password change live in small expandable
 * sections instead of a wall of forms. Staff portals keep the shared
 * ProfilePage — this one is customer-only.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Car,
  Check,
  ChevronRight,
  FileText,
  Gift,
  KeyRound,
  LifeBuoy,
  LogOut,
  MapPin,
  Pencil,
  type LucideIcon,
} from "lucide-react";
import { userApi, vehicleApi, addressApi } from "../../api/profile";
import { authApi } from "../../api/auth";
import { subscriptionApi } from "../../api/engagement";
import { Button, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";

function Row({
  icon: Icon,
  label,
  meta,
  to,
  onClick,
  danger,
  last,
}: {
  icon: LucideIcon;
  label: string;
  meta?: string;
  to?: string;
  onClick?: () => void;
  danger?: boolean;
  last?: boolean;
}) {
  const inner = (
    <>
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${danger ? "bg-red-50 text-[var(--color-error)]" : "bg-gray-100 text-black"}`}>
        <Icon className="h-[17px] w-[17px]" />
      </span>
      <span className={`flex-1 text-left text-sm font-semibold ${danger ? "text-[var(--color-error)]" : "text-black"}`}>{label}</span>
      {meta && <span className="text-xs text-gray-400">{meta}</span>}
      {!danger && <ChevronRight className="h-4 w-4 shrink-0 text-gray-300" />}
    </>
  );
  const cls = `flex w-full items-center gap-3.5 px-4 py-3.5 transition-colors hover:bg-[#FAFAFA] ${last ? "" : "border-b border-[#F3E5B5]"}`;
  return to ? (
    <Link to={to} className={cls}>
      {inner}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={cls}>
      {inner}
    </button>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-black">{title}</p>
      <div className="overflow-hidden rounded-2xl border border-[#F3E5B5] bg-white">{children}</div>
    </div>
  );
}

export default function CustomerProfilePage() {
  const { user, logout, refreshUser } = useAuth();
  const confirm = useConfirm();
  const { data: vehicles } = useQuery({ queryKey: ["vehicles"], queryFn: vehicleApi.list });
  const { data: addresses } = useQuery({ queryKey: ["addresses"], queryFn: addressApi.list });
  const { data: subs } = useQuery({ queryKey: ["my-subscriptions"], queryFn: subscriptionApi.mySubscriptions });
  const activeSub = (subs || []).find((s) => s.effective_status === "active");

  const [editingName, setEditingName] = useState(false);
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [nameError, setNameError] = useState("");

  const [changingPassword, setChangingPassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordMsg, setPasswordMsg] = useState("");
  const [passwordError, setPasswordError] = useState("");

  const saveName = useMutation({
    mutationFn: () => userApi.updateProfile({ full_name: fullName }),
    onSuccess: async () => {
      setEditingName(false);
      setNameError("");
      await refreshUser();
    },
    onError: (err) => setNameError(getErrorMessage(err)),
  });

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

  const initial = (user?.full_name || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      {/* Identity */}
      <div className="rounded-2xl border border-[#F3E5B5] bg-white p-4 sm:p-5">
        <div className="flex items-center gap-4">
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gray-100 font-display text-xl font-bold text-black">
            {initial}
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-display text-lg font-bold text-black">{user?.full_name}</p>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              {user?.phone && <span className="font-mono-num text-xs text-gray-400">{user.phone}</span>}
              {user?.phone && user?.phone_verified && (
                <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-bold text-green-700">
                  <Check className="h-2.5 w-2.5" strokeWidth={3.5} /> Verified
                </span>
              )}
              {user?.email && <span className="truncate text-xs text-gray-400">{user.email}</span>}
            </div>
          </div>
          <button
            type="button"
            aria-label="Edit name"
            onClick={() => {
              setFullName(user?.full_name || "");
              setEditingName((v) => !v);
            }}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#F3E5B5] text-black transition-colors hover:border-[#E8A900]/60"
          >
            <Pencil className="h-4 w-4" />
          </button>
        </div>
        {editingName && (
          <div className="mt-4 space-y-3 border-t border-[#F3E5B5] pt-4">
            <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} autoFocus />
            {nameError && <p className="text-sm text-[var(--color-error)]">{nameError}</p>}
            <div className="flex gap-2">
              <Button size="sm" isLoading={saveName.isPending} onClick={() => saveName.mutate()}>
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditingName(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>

      <Group title="Account">
        <Row icon={Car} label="My vehicles" meta={vehicles?.length ? String(vehicles.length) : undefined} to="/app/vehicles" />
        <Row icon={MapPin} label="Saved addresses" meta={addresses?.length ? String(addresses.length) : undefined} to="/app/addresses" />
        <Row
          icon={Gift}
          label="My subscription"
          meta={activeSub ? `${activeSub.remaining_service_count} washes left` : "No active plan"}
          to="/app/subscriptions"
          last
        />
      </Group>

      <Group title="Security">
        <Row icon={KeyRound} label="Change password" onClick={() => setChangingPassword((v) => !v)} last={!changingPassword} />
        {changingPassword && (
          <div className="space-y-3 border-t border-[#F3E5B5] px-4 py-4">
            <Input label="Current password" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
            <Input label="New password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} hint="At least 8 characters" />
            {passwordError && <p className="text-sm text-[var(--color-error)]">{passwordError}</p>}
            <Button size="sm" isLoading={changePassword.isPending} onClick={() => changePassword.mutate()}>
              Update password
            </Button>
          </div>
        )}
      </Group>
      {passwordMsg && <p className="text-sm text-[var(--color-success)]">{passwordMsg}</p>}

      <Group title="More">
        <Row icon={LifeBuoy} label="Support" to="/app/support" />
        <Row icon={FileText} label="Cancellation policy" to="/cancellation-policy" />
        <Row
            icon={LogOut}
            label="Log out"
            onClick={async () => {
              if (await confirm({ title: "Log out?", message: "You'll need your password (or an OTP) to sign back in.", tone: "danger" })) logout();
            }}
            danger
            last
          />
      </Group>
    </div>
  );
}
