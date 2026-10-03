/**
 * Captain · Profile — who he is (+ rating), KYC status (the form opens
 * when there's something to submit; the CENTER MANAGER verifies it and it
 * locks once verified), today's clock in/out, language, password, log out.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Building2, Camera, Check, ChevronDown, ChevronRight, Clock, KeyRound, Languages, Loader2, LogOut, ShieldAlert, Star, Upload } from "lucide-react";
import { authApi } from "../../api/auth";
import { staffDirectoryApi } from "../../api/admin";
import { uploadApi } from "../../api/upload";
import { kycApi, type CaptainKyc } from "../../api/staffOps";
import { useAuth } from "../../context/AuthContext";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { getErrorMessage } from "../../lib/api-client";
import { AttendanceCard } from "../../components/captain/Attendance";
import { Btn, PageTitle, Panel, Pill, Sheet } from "../../components/captain/ui";

const field =
  "w-full rounded-2xl border border-[#E4E9F1] px-4 text-[15px] text-[#0E1A33] outline-none focus:border-[#0A66F0] focus:ring-2 focus:ring-[#E8F0FE] disabled:bg-[#EEF3FA] disabled:text-[#5F6878]";

const KYC_TONE = { pending: "gray", submitted: "amber", verified: "green", rejected: "red" } as const;

function DocUpload({ label, url, disabled, onUploaded }: { label: string; url?: string | null; disabled: boolean; onUploaded: (url: string) => void }) {
  const { t } = useCaptainTranslation();
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="flex min-h-[56px] items-center justify-between gap-3 rounded-2xl border border-[#E4E9F1] px-4 py-2">
      <div className="min-w-0">
        <p className="text-sm font-bold text-[#0E1A33]">{label}</p>
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" className="text-xs font-semibold text-[#0A66F0]">
            {t("captain.v2.viewDoc")}
          </a>
        ) : (
          <p className="text-xs text-[#5F6878]">{t("captain.profile.clearPhoto")}</p>
        )}
        {error && <p className="text-xs text-[#B91C1C]">{error}</p>}
      </div>
      {!disabled && (
        <button
          type="button"
          disabled={busy}
          onClick={() => ref.current?.click()}
          className="flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-xl bg-[#E8F0FE] px-3 text-sm font-bold text-[#0A66F0]"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} {url ? t("captain.profile.replace") : t("captain.profile.upload")}
        </button>
      )}
      <input
        ref={ref}
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
            onUploaded(await uploadApi.document(file));
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
  const { t, language, setLanguage } = useCaptainTranslation();
  const { user, logout, refreshUser } = useAuth();
  const queryClient = useQueryClient();
  const photoRef = useRef<HTMLInputElement>(null);
  const [photoUploading, setPhotoUploading] = useState(false);

  const { data: kyc } = useQuery({ queryKey: ["my-kyc"], queryFn: kycApi.my });
  const { data: perf } = useQuery({ queryKey: ["my-performance"], queryFn: staffDirectoryApi.myPerformance });
  const [form, setForm] = useState<Partial<CaptainKyc>>({});
  const [kycOpen, setKycOpen] = useState<boolean | null>(null);
  const [formError, setFormError] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (kyc) setForm(kyc);
  }, [kyc]);

  const status = kyc?.status || "pending";
  const locked = status === "verified";
  // Open by default only when there's something for him to do.
  const showKyc = kycOpen ?? (status === "pending" || status === "rejected");
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

  const [pwOpen, setPwOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwError, setPwError] = useState("");
  const [pwDone, setPwDone] = useState(false);
  const changePassword = useMutation({
    mutationFn: () => authApi.changePassword({ current_password: currentPassword, new_password: newPassword }),
    onSuccess: async () => {
      setPwError("");
      setCurrentPassword("");
      setNewPassword("");
      setPwOpen(false);
      setPwDone(true);
      await refreshUser();
    },
    onError: (err) => setPwError(getErrorMessage(err)),
  });

  const initial = (user?.full_name || "?").trim().charAt(0).toUpperCase();
  const rowCls = "flex min-h-[56px] w-full items-center gap-3 px-4 text-left active:bg-[#EEF3FA]";

  return (
    <div className="space-y-4">
      <PageTitle>{t("captain.v2.tab.profile")}</PageTitle>

      <Panel className="flex items-center gap-4 p-4">
        <button
          type="button"
          onClick={() => !locked && !photoUploading && photoRef.current?.click()}
          className="relative flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[#E8F0FE] text-xl font-extrabold text-[#0A66F0]"
          aria-label={t("captain.profile.addPhoto")}
        >
          {form.photo_url ? <img src={form.photo_url} alt="" className="h-full w-full object-cover" /> : initial}
          {!locked && (
            <span className="absolute inset-x-0 bottom-0 flex justify-center bg-[#0E1A33]/55 py-0.5">
              {photoUploading ? <Loader2 className="h-3 w-3 animate-spin text-white" /> : <Camera className="h-3 w-3 text-white" />}
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
            setPhotoUploading(true);
            try {
              set({ photo_url: await uploadApi.photo(file) });
              setKycOpen(true);
            } catch (err) {
              setFormError(getErrorMessage(err));
            } finally {
              setPhotoUploading(false);
            }
          }}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-lg font-extrabold text-[#0E1A33]">{user?.full_name}</p>
          <p className="tabular-nums text-sm text-[#5F6878]">{[user?.employee_id, user?.phone].filter(Boolean).join(" · ")}</p>
          {perf && (
            <p className="mt-0.5 flex items-center gap-1 text-sm font-semibold text-[#0E1A33]">
              <Star className="h-4 w-4 fill-[#FFD21F] text-[#FFD21F]" /> {perf.total_reviews ? perf.average_rating.toFixed(1) : "—"}
              <span className="text-[#5F6878]">· {t("captain.v2.nJobs").replace("{n}", String(perf.total_jobs_completed ?? 0))}</span>
            </p>
          )}
        </div>
      </Panel>

      <div>
        <h2 className="mb-2 text-[15px] font-extrabold text-[#0E1A33]">{t("captain.v2.attendance")}</h2>
        <AttendanceCard />
        <Link to="/captain/attendance" className="mt-1 flex min-h-[44px] items-center gap-1 px-1 text-sm font-bold text-[#0A66F0]">
          <Clock className="h-4 w-4" /> {t("captain.v2.historyLeave")} <ChevronRight className="h-4 w-4" />
        </Link>
      </div>

      <Panel className="overflow-hidden">
        <button type="button" className={rowCls} onClick={() => setKycOpen(!showKyc)} aria-expanded={showKyc}>
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0A66F0]">
            {locked ? <BadgeCheck className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
          </span>
          <span className="flex-1 text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.kyc")}</span>
          <Pill tone={KYC_TONE[status]}>{t(`captain.profile.kyc.${status}` as Parameters<typeof t>[0])}</Pill>
          <ChevronDown className={`h-5 w-5 text-[#A3AAB6] transition-transform ${showKyc ? "rotate-180" : ""}`} />
        </button>
        {showKyc && (
          <div className="space-y-2.5 border-t border-[#E4E9F1] p-4">
            {status === "rejected" && kyc?.review_note && (
              <p className="rounded-xl bg-[#FDECEC] px-3.5 py-2.5 text-sm text-[#B91C1C]">
                {t("captain.profile.managersNote")}: {kyc.review_note}
              </p>
            )}
            {locked && <p className="text-sm font-semibold text-[#15803D]">{t("captain.v2.kycLocked")}</p>}
            <label className="block text-xs font-bold text-[#5F6878]">
              {t("captain.profile.aadhaarNumber")}
              <input className={`${field} mt-1 h-12`} inputMode="numeric" placeholder="12 digits" value={form.aadhaar_number || ""} disabled={locked}
                onChange={(e) => set({ aadhaar_number: e.target.value.replace(/\D/g, "").slice(0, 12) })} />
            </label>
            <label className="block text-xs font-bold text-[#5F6878]">
              {t("captain.profile.panNumber")}
              <input className={`${field} mt-1 h-12`} placeholder="ABCDE1234F" value={form.pan_number || ""} disabled={locked}
                onChange={(e) => set({ pan_number: e.target.value.toUpperCase().slice(0, 10) })} />
            </label>
            <DocUpload label={t("captain.profile.aadhaarPhoto")} url={form.aadhaar_doc_url} disabled={locked} onUploaded={(url) => set({ aadhaar_doc_url: url })} />
            <DocUpload label={t("captain.profile.panPhoto")} url={form.pan_doc_url} disabled={locked} onUploaded={(url) => set({ pan_doc_url: url })} />
            <label className="block text-xs font-bold text-[#5F6878]">
              {t("captain.profile.localAddress")}
              <textarea className={`${field} mt-1 py-3`} rows={2} value={form.local_address || ""} disabled={locked} onChange={(e) => set({ local_address: e.target.value })} />
            </label>
            <label className="flex min-h-[44px] items-center gap-2.5 text-sm font-semibold text-[#0E1A33]">
              <input type="checkbox" className="h-5 w-5 accent-[#0A66F0]" checked={!!form.same_as_local} disabled={locked} onChange={(e) => set({ same_as_local: e.target.checked })} />
              {t("captain.profile.sameAsLocalLabel")}
            </label>
            {!form.same_as_local && (
              <label className="block text-xs font-bold text-[#5F6878]">
                {t("captain.profile.permanentAddress")}
                <textarea className={`${field} mt-1 py-3`} rows={2} value={form.permanent_address || ""} disabled={locked} onChange={(e) => set({ permanent_address: e.target.value })} />
              </label>
            )}
            {formError && <p className="text-sm font-medium text-[#B91C1C]">{formError}</p>}
            {saved && (
              <p className="flex items-center gap-1.5 text-sm font-semibold text-[#15803D]">
                <Check className="h-4 w-4" /> {t("captain.v2.kycSent")}
              </p>
            )}
            {!locked && (
              <Btn className="w-full" loading={submitKyc.isPending} onClick={() => submitKyc.mutate()}>
                {status === "pending" ? t("captain.profile.submitForVerification") : t("captain.profile.resubmit")}
              </Btn>
            )}
          </div>
        )}
      </Panel>

      <Panel className="divide-y divide-[#E4E9F1] overflow-hidden">
        <button type="button" className={rowCls} onClick={() => setLanguage(language === "en" ? "hi" : "en")}>
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0A66F0]">
            <Languages className="h-5 w-5" />
          </span>
          <span className="flex-1 text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.language")}</span>
          <span className="text-sm font-bold text-[#0A66F0]">{language === "en" ? "English → हिन्दी" : "हिन्दी → English"}</span>
        </button>
        <Link to="/captain/societies" className={rowCls}>
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0A66F0]">
            <Building2 className="h-5 w-5" />
          </span>
          <span className="flex-1 text-[15px] font-bold text-[#0E1A33]">{t("captain.v2.societies")}</span>
          <ChevronRight className="h-5 w-5 text-[#A3AAB6]" />
        </Link>
        <button type="button" className={rowCls} onClick={() => setPwOpen(true)}>
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#EEF3FA] text-[#0A66F0]">
            <KeyRound className="h-5 w-5" />
          </span>
          <span className="flex-1 text-[15px] font-bold text-[#0E1A33]">{t("captain.profile.changePassword")}</span>
          {pwDone && <Pill tone="green">{t("captain.v2.updated")}</Pill>}
        </button>
      </Panel>

      <Btn variant="outline" className="w-full !text-[#B91C1C]" onClick={logout}>
        <LogOut className="h-5 w-5" /> {t("captain.nav.logout")}
      </Btn>

      <Sheet open={pwOpen} onClose={() => setPwOpen(false)} title={t("captain.profile.changePassword")}>
        <div className="space-y-2">
          <input className={`${field} h-12`} type="password" autoComplete="current-password" placeholder={t("captain.profile.currentPassword")} value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
          <input className={`${field} h-12`} type="password" autoComplete="new-password" placeholder={t("captain.profile.newPassword")} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
        </div>
        {pwError && <p className="mt-2 text-sm font-medium text-[#B91C1C]">{pwError}</p>}
        <Btn className="mt-3 w-full" disabled={!currentPassword || newPassword.length < 8} loading={changePassword.isPending} onClick={() => changePassword.mutate()}>
          {t("captain.common.save")}
        </Btn>
      </Sheet>
    </div>
  );
}
