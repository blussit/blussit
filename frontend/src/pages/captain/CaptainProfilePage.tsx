/**
 * Captain profile — identity + the KYC/background-verification packet
 * (photo, Aadhaar, PAN, doc images, local/permanent address). The captain
 * submits; their CENTER MANAGER reviews and marks verified/rejected.
 * Editing locks once verified. Staff manager/admin keep the shared
 * ProfilePage — this one is captain-only.
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Camera, Check, Clock, FileText, KeyRound, LogOut, ShieldAlert, Upload } from "lucide-react";
import { authApi } from "../../api/auth";
import { uploadApi } from "../../api/upload";
import { kycApi, type CaptainKyc } from "../../api/staffOps";
import { Button, Input } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../lib/api-client";

const STATUS_CHIP: Record<string, { label: string; cls: string }> = {
  pending: { label: "Not verified", cls: "bg-gray-100 text-gray-600" },
  submitted: { label: "Under review", cls: "bg-amber-100 text-amber-700" },
  verified: { label: "Verified", cls: "bg-green-100 text-green-700" },
  rejected: { label: "Needs changes", cls: "bg-red-100 text-red-700" },
};

function DocUpload({ label, url, disabled, onUploaded }: { label: string; url?: string | null; disabled: boolean; onUploaded: (url: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="rounded-xl border border-[#F3E5B5] p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-black">{label}</p>
          {url ? (
            <a href={url} target="_blank" rel="noreferrer" className="text-xs font-medium text-black hover:underline">
              View uploaded document
            </a>
          ) : (
            <p className="text-xs text-gray-400">Clear photo or scan</p>
          )}
        </div>
        {!disabled && (
          <Button size="sm" variant="outline" isLoading={busy} onClick={() => inputRef.current?.click()}>
            <Upload className="h-3.5 w-3.5" /> {url ? "Replace" : "Upload"}
          </Button>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-[var(--color-error)]">{error}</p>}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file) return;
          setBusy(true);
          setError("");
          try {
            onUploaded(await uploadApi.photo(file));
          } catch (err) {
            setError(getErrorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      />
    </div>
  );
}

export default function CaptainProfilePage() {
  const { user, logout, refreshUser } = useAuth();
  const queryClient = useQueryClient();
  const photoRef = useRef<HTMLInputElement>(null);

  const { data: kyc } = useQuery({ queryKey: ["my-kyc"], queryFn: kycApi.my });
  const [form, setForm] = useState<Partial<CaptainKyc>>({});
  const [formError, setFormError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (kyc) setForm(kyc);
  }, [kyc]);

  const locked = kyc?.status === "verified";
  const set = (patch: Partial<CaptainKyc>) => {
    setForm((f) => ({ ...f, ...patch }));
    setSaved(false);
  };

  const submitKyc = useMutation({
    mutationFn: () =>
      kycApi.submit({
        photo_url: form.photo_url || null,
        aadhaar_number: form.aadhaar_number || null,
        pan_number: form.pan_number ? form.pan_number.toUpperCase() : null,
        aadhaar_doc_url: form.aadhaar_doc_url || null,
        pan_doc_url: form.pan_doc_url || null,
        local_address: form.local_address || null,
        permanent_address: form.same_as_local ? form.local_address || null : form.permanent_address || null,
        same_as_local: !!form.same_as_local,
      }),
    onSuccess: () => {
      setFormError("");
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ["my-kyc"] });
    },
    onError: (e) => setFormError(getErrorMessage(e)),
  });

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

  const status = kyc?.status || "pending";
  const chip = STATUS_CHIP[status];
  const initial = (user?.full_name || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      {/* Identity */}
      <div className="rounded-2xl border border-[#F3E5B5] bg-white p-4 sm:p-5">
        <div className="flex items-center gap-4">
          <button
            type="button"
            onClick={() => !locked && photoRef.current?.click()}
            className="relative flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-full bg-gray-100 font-display text-xl font-bold text-black"
            title={locked ? undefined : "Add your photo"}
          >
            {form.photo_url ? <img src={form.photo_url} alt="Profile" className="h-full w-full object-cover" /> : initial}
            {!locked && (
              <span className="absolute bottom-0 inset-x-0 flex items-center justify-center bg-black/50 py-0.5">
                <Camera className="h-3 w-3 text-white" />
              </span>
            )}
          </button>
          <input
            ref={photoRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              try {
                set({ photo_url: await uploadApi.photo(file) });
              } catch (err) {
                setFormError(getErrorMessage(err));
              }
            }}
          />
          <div className="min-w-0 flex-1">
            <p className="font-display text-lg font-bold text-black">{user?.full_name}</p>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              {user?.employee_id && (
                <span className="rounded-full bg-black px-2 py-0.5 font-mono-num text-[10px] font-bold text-white">{user.employee_id}</span>
              )}
              {user?.phone && <span className="font-mono-num text-xs text-gray-400">{user.phone}</span>}
              <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${chip.cls}`}>
                {status === "verified" ? <BadgeCheck className="h-3 w-3" /> : status === "submitted" ? <Clock className="h-3 w-3" /> : <ShieldAlert className="h-3 w-3" />}
                {chip.label}
              </span>
            </div>
          </div>
        </div>
        {status === "rejected" && kyc?.review_note && (
          <p className="mt-3 rounded-xl bg-red-50 px-3.5 py-2.5 text-xs text-red-700">Manager's note: {kyc.review_note}</p>
        )}
        {status === "pending" && (
          <p className="mt-3 rounded-xl bg-[#FAFAFA] px-3.5 py-2.5 text-xs text-gray-600">
            Complete your background verification below — your manager reviews and approves it.
          </p>
        )}
      </div>

      {/* KYC */}
      <div className="space-y-2">
        <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-black">Background verification</p>
        <div className="space-y-3 rounded-2xl border border-[#F3E5B5] bg-white p-4">
          {locked && (
            <p className="flex items-center gap-1.5 text-xs text-green-700">
              <BadgeCheck className="h-3.5 w-3.5" /> Verified — ask your manager to reopen if anything changed.
            </p>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input
              label="Aadhaar number"
              placeholder="12 digits"
              value={form.aadhaar_number || ""}
              disabled={locked}
              onChange={(e) => set({ aadhaar_number: e.target.value.replace(/\D/g, "").slice(0, 12) })}
            />
            <Input
              label="PAN number"
              placeholder="ABCDE1234F"
              value={form.pan_number || ""}
              disabled={locked}
              onChange={(e) => set({ pan_number: e.target.value.toUpperCase().slice(0, 10) })}
            />
          </div>
          <DocUpload label="Aadhaar card photo" url={form.aadhaar_doc_url} disabled={locked} onUploaded={(url) => set({ aadhaar_doc_url: url })} />
          <DocUpload label="PAN card photo" url={form.pan_doc_url} disabled={locked} onUploaded={(url) => set({ pan_doc_url: url })} />
          <div>
            <label className="mb-1 block text-sm font-medium text-[var(--color-text-primary)]">Local address</label>
            <textarea
              className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:bg-gray-50"
              rows={2}
              value={form.local_address || ""}
              disabled={locked}
              onChange={(e) => set({ local_address: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-primary)]">
            <input
              type="checkbox"
              checked={!!form.same_as_local}
              disabled={locked}
              onChange={(e) => set({ same_as_local: e.target.checked })}
              className="h-4 w-4 rounded border-gray-300 accent-[#E8A900]"
            />
            Permanent address is the same as local
          </label>
          {!form.same_as_local && (
            <div>
              <label className="mb-1 block text-sm font-medium text-[var(--color-text-primary)]">Permanent address</label>
              <textarea
                className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] disabled:bg-gray-50"
                rows={2}
                value={form.permanent_address || ""}
                disabled={locked}
                onChange={(e) => set({ permanent_address: e.target.value })}
              />
            </div>
          )}
          {formError && <p className="text-sm text-[var(--color-error)]">{formError}</p>}
          {saved && (
            <p className="flex items-center gap-1.5 text-sm text-green-700">
              <Check className="h-4 w-4" /> Submitted — your manager will review it.
            </p>
          )}
          {!locked && (
            <Button className="w-full" isLoading={submitKyc.isPending} onClick={() => submitKyc.mutate()}>
              <FileText className="h-4 w-4" /> {status === "pending" ? "Submit for verification" : "Resubmit"}
            </Button>
          )}
        </div>
      </div>

      {/* Security */}
      <div className="space-y-2">
        <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-black">Security</p>
        <div className="rounded-2xl border border-[#F3E5B5] bg-white p-4">
          {!changingPassword ? (
            <button type="button" onClick={() => setChangingPassword(true)} className="flex w-full items-center gap-3 text-left">
              <span className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-gray-100 text-black">
                <KeyRound className="h-[17px] w-[17px]" />
              </span>
              <span className="flex-1 text-sm font-semibold text-black">Change password</span>
            </button>
          ) : (
            <div className="space-y-3">
              <Input label="Current password" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
              <Input label="New password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
              {passwordError && <p className="text-sm text-[var(--color-error)]">{passwordError}</p>}
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => setChangingPassword(false)}>
                  Cancel
                </Button>
                <Button className="flex-1" isLoading={changePassword.isPending} disabled={!currentPassword || newPassword.length < 8} onClick={() => changePassword.mutate()}>
                  Update
                </Button>
              </div>
            </div>
          )}
          {passwordMsg && !changingPassword && <p className="mt-2 text-xs text-green-700">{passwordMsg}</p>}
        </div>
      </div>

      <button
        type="button"
        onClick={logout}
        className="flex w-full items-center justify-center gap-2 rounded-2xl border border-red-200 bg-white py-3 text-sm font-bold text-[var(--color-error)] transition-colors hover:bg-red-50"
      >
        <LogOut className="h-4 w-4" /> Log out
      </button>
    </div>
  );
}
