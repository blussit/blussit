import { useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Building2, Camera, Eye, EyeOff, KeyRound, LogOut, Mail, Phone, RefreshCw, ShieldCheck, User } from "lucide-react";
import { userApi } from "../../api/profile";
import { authApi } from "../../api/auth";
import { adminServiceCenterApi } from "../../api/admin";
import { uploadApi } from "../../api/upload";
import { Badge, Button, Card, CardBody, CardHeader, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { format } from "../../lib/date";

/** Manager/admin account page — identity card up top (photo, role, store),
 * then personal info and security as their own sections. */
export default function ProfilePage() {
  const { user, refreshUser, logout } = useAuth();
  const confirm = useConfirm();
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [profileMsg, setProfileMsg] = useState("");
  const [profileError, setProfileError] = useState("");
  const [photoUploading, setPhotoUploading] = useState(false);
  const photoInputRef = useRef<HTMLInputElement>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [passwordMsg, setPasswordMsg] = useState("");
  const [passwordError, setPasswordError] = useState("");

  // The store this manager runs — pure context, and the header's most
  // useful line for someone juggling several browser tabs.
  const { data: center } = useQuery({
    queryKey: ["own-center", user?.service_center_id],
    queryFn: () => adminServiceCenterApi.get(user!.service_center_id!),
    enabled: user?.role === "manager" && !!user?.service_center_id,
  });

  const updateProfileMutation = useMutation({
    mutationFn: (payload: { full_name?: string; profile_image?: string }) => userApi.updateProfile(payload),
    onSuccess: async () => {
      setProfileMsg("Saved.");
      setProfileError("");
      await refreshUser();
    },
    onError: (err) => setProfileError(getErrorMessage(err)),
  });

  const changePasswordMutation = useMutation({
    mutationFn: () => authApi.changePassword({ current_password: currentPassword, new_password: newPassword }),
    onSuccess: async () => {
      setPasswordMsg("Password changed successfully");
      setPasswordError("");
      setCurrentPassword("");
      setNewPassword("");
      // Clears must_change_password client-side so the redirect in
      // ProtectedRoute stops firing and the rest of the app is reachable.
      await refreshUser();
    },
    onError: (err) => setPasswordError(getErrorMessage(err)),
  });

  const initials = (user?.full_name || "?")
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text-primary)]">Profile & security</h1>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Manage your account information.</p>
      </div>

      {user?.must_change_password && (
        <div className="flex items-start gap-3 rounded-xl border-2 border-amber-400 bg-amber-50 p-4">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
          <div>
            <p className="font-semibold text-[var(--color-text-primary)]">Set a new password to continue</p>
            <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
              This account was created with a temporary password. Please set your own below before using the rest of the app.
            </p>
          </div>
        </div>
      )}

      {/* Identity card */}
      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-5">
          <button
            type="button"
            onClick={() => photoInputRef.current?.click()}
            className="group relative h-20 w-20 shrink-0 overflow-hidden rounded-full border border-gray-200"
            title="Change photo"
          >
            {user?.profile_image ? (
              <img src={user.profile_image} alt={user.full_name} className="h-full w-full object-cover" />
            ) : (
              <span className="flex h-full w-full items-center justify-center bg-[var(--color-primary-light)] text-xl font-bold text-[var(--color-primary)]">
                {initials}
              </span>
            )}
            <span className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition-opacity group-hover:opacity-100">
              {photoUploading ? <RefreshCw className="h-5 w-5 animate-spin text-white" /> : <Camera className="h-5 w-5 text-white" />}
            </span>
          </button>
          <input
            ref={photoInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              setPhotoUploading(true);
              try {
                const url = await uploadApi.photo(file);
                updateProfileMutation.mutate({ profile_image: url });
              } catch (err) {
                setProfileError(getErrorMessage(err));
              } finally {
                setPhotoUploading(false);
              }
            }}
          />
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-2 text-lg font-bold text-[var(--color-text-primary)]">
              {user?.full_name}
              <Badge tone="neutral">
                <ShieldCheck className="h-3 w-3" /> <span className="capitalize">{user?.role}</span>
              </Badge>
            </p>
            <div className="mt-1 space-y-0.5 text-sm text-[var(--color-text-secondary)]">
              {center && (
                <p className="flex items-center gap-1.5">
                  <Building2 className="h-3.5 w-3.5" /> {center.name}
                  {center.location?.city ? `, ${center.location.city}` : ""}
                </p>
              )}
              {user?.email && (
                <p className="flex items-center gap-1.5">
                  <Mail className="h-3.5 w-3.5" /> {user.email}
                </p>
              )}
              {user?.phone && (
                <p className="flex items-center gap-1.5">
                  <Phone className="h-3.5 w-3.5" /> {user.phone}
                </p>
              )}
              {user?.created_at && <p className="text-xs">Member of the team since {format(user.created_at)}</p>}
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader className="flex items-center gap-2">
          <User className="h-4 w-4 text-[var(--color-primary)]" />
          <h2 className="font-semibold text-[var(--color-text-primary)]">Personal information</h2>
        </CardHeader>
        <CardBody className="space-y-4">
          <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input label="Email" value={user?.email || "—"} disabled hint="Contact an admin to change login details." />
            <Input label="Phone" value={user?.phone || "—"} disabled />
          </div>
          {profileMsg && <p className="text-sm text-[var(--color-success)]">{profileMsg}</p>}
          {profileError && <p className="text-sm text-[var(--color-error)]">{profileError}</p>}
          <Button
            isLoading={updateProfileMutation.isPending}
            disabled={!fullName.trim() || fullName === user?.full_name}
            onClick={() => updateProfileMutation.mutate({ full_name: fullName })}
          >
            Save changes
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-[var(--color-primary)]" />
          <h2 className="font-semibold text-[var(--color-text-primary)]">Change password</h2>
        </CardHeader>
        <CardBody className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Input
              label="Current password"
              type={showPasswords ? "text" : "password"}
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
            <Input
              label="New password"
              type={showPasswords ? "text" : "password"}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              hint="At least 8 characters."
            />
          </div>
          <button
            type="button"
            className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            onClick={() => setShowPasswords((s) => !s)}
          >
            {showPasswords ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            {showPasswords ? "Hide passwords" : "Show passwords"}
          </button>
          {passwordMsg && <p className="text-sm text-[var(--color-success)]">{passwordMsg}</p>}
          {passwordError && <p className="text-sm text-[var(--color-error)]">{passwordError}</p>}
          <Button
            isLoading={changePasswordMutation.isPending}
            disabled={!currentPassword || newPassword.length < 8}
            onClick={() => changePasswordMutation.mutate()}
          >
            Update password
          </Button>
        </CardBody>
      </Card>

      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-semibold text-[var(--color-text-primary)]">Session</p>
            <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">Signed in as {user?.email || user?.phone}.</p>
          </div>
          <Button
            variant="outline"
            onClick={async () => {
              if (await confirm({ title: "Log out?", message: "You'll need your password to sign back in." })) logout();
            }}
          >
            <LogOut className="h-4 w-4" /> Log out
          </Button>
        </div>
      </Card>
    </div>
  );
}
