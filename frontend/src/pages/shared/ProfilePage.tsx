import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, KeyRound, User } from "lucide-react";
import { userApi } from "../../api/profile";
import { authApi } from "../../api/auth";
import { Button, Card, CardBody, CardHeader, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";

export default function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [profileMsg, setProfileMsg] = useState("");
  const [profileError, setProfileError] = useState("");

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordMsg, setPasswordMsg] = useState("");
  const [passwordError, setPasswordError] = useState("");

  const updateProfileMutation = useMutation({
    mutationFn: () => userApi.updateProfile({ full_name: fullName }),
    onSuccess: async () => {
      setProfileMsg("Profile updated successfully");
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

      <Card>
        <CardHeader className="flex items-center gap-2">
          <User className="h-4 w-4 text-[var(--color-primary)]" />
          <h2 className="font-semibold text-[var(--color-text-primary)]">Personal information</h2>
        </CardHeader>
        <CardBody className="space-y-4">
          <Input label="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <Input label="Email" value={user?.email || "—"} disabled />
          <Input label="Phone" value={user?.phone || "—"} disabled />
          <Input label="Role" value={user?.role || ""} disabled className="capitalize" />
          {profileMsg && <p className="text-sm text-[var(--color-success)]">{profileMsg}</p>}
          {profileError && <p className="text-sm text-[var(--color-error)]">{profileError}</p>}
          <Button isLoading={updateProfileMutation.isPending} onClick={() => updateProfileMutation.mutate()}>
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
          <Input label="Current password" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
          <Input label="New password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
          {passwordMsg && <p className="text-sm text-[var(--color-success)]">{passwordMsg}</p>}
          {passwordError && <p className="text-sm text-[var(--color-error)]">{passwordError}</p>}
          <Button isLoading={changePasswordMutation.isPending} onClick={() => changePasswordMutation.mutate()}>
            Update password
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}
