/**
 * Landing Plans section: "For housing societies" — a short pitch and a
 * request form (society, area, pincode, contact, phone, approx. cars).
 * The request goes to the manager of the center serving that pincode
 * (admin when none does); nothing comes back but a thank-you.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Building2, CalendarCheck, CheckCircle2, Crown, Sparkles, UserCheck } from "lucide-react";
import { Modal } from "../../ui";
import { societyLeadApi } from "../../../api/society";
import { getErrorMessage } from "../../../lib/api-client";
import { validateIndianMobile } from "../../../lib/validators";
import { INR } from "./shared";
import { trackLead } from "../../../lib/metaPixel";

const FALLBACK_FROM = 1649;
const field =
  "h-12 w-full rounded-[14px] border border-[#E4E9F1] bg-white px-4 text-[15px] text-[#0E1A33] outline-none transition placeholder:text-[#9AA3B2] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10";
const fieldLabel = "mb-1.5 block text-[13px] font-semibold text-[#0E1A33]";

export function SocietyRequestCard() {
  const [open, setOpen] = useState(false);
  const info = useQuery({ queryKey: ["society-pitch"], queryFn: societyLeadApi.info, staleTime: 10 * 60 * 1000, retry: false });
  const from = info.data?.from_price ?? FALLBACK_FROM;

  return (
    <div
      // The footer's "Society Plans" button lands here (/plans#society).
      id="society"
      className="group relative flex scroll-mt-24 flex-col overflow-hidden rounded-[22px] border-2 border-[#0A66F0]/35 bg-[#EEF3FA] shadow-[0_10px_30px_rgba(10,102,240,0.12)] sm:flex-row lg:h-full lg:flex-col"
      data-testid="landing-society-card"
    >
      <span className="absolute right-3 top-3 z-20 inline-flex items-center gap-1.5 rounded-full bg-[#FDE9A6] px-3 py-1 text-[11px] font-bold text-[#071A3D] shadow-sm">
        <Crown className="h-3.5 w-3.5" strokeWidth={2.5} />
        Most Popular
      </span>

      <div className="relative z-10 flex flex-col flex-1 p-5 sm:w-[56%] sm:flex-none sm:p-6 lg:w-full lg:flex-1 lg:p-7">
        <div>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-[1.2px] text-[#0A66F0] shadow-sm">
            <Building2 className="h-3.5 w-3.5" /> For Housing Societies
          </span>
          <h3 className="mt-3 font-display text-[22px] font-extrabold leading-tight text-[#0E1A33] sm:text-[24px]">
            Every Car In Your Society, Washed Daily
          </h3>
          <p className="mt-2 text-[13px] leading-snug text-[#5F6878]">
            A dedicated captain comes every morning, plus premium washes each month. Residents join from one link.
          </p>
        </div>

        <div className="mt-5 lg:mt-6">
          <p className="text-[#0E1A33] flex items-baseline gap-1.5">
            <span className="text-[12px] text-[#5F6878]">From</span>
            <span className="font-display text-[30px] font-extrabold leading-none text-[#0A66F0] sm:text-[34px]">{INR(from)}</span>
            <span className="text-[13px] text-[#5F6878]">/ Car / Month</span>
          </p>
        </div>

        <ul className="mb-5 mt-5 space-y-3">
          <li className="flex items-center gap-2"><UserCheck className="h-[18px] w-[18px] shrink-0 text-[#0A66F0]" /> <span className="text-[13px] font-bold text-[#0E1A33]">Daily Wash By Your Captain</span></li>
          <li className="flex items-center gap-2"><Sparkles className="h-[18px] w-[18px] shrink-0 text-[#0A66F0]" /> <span className="text-[13px] font-bold text-[#0E1A33]">Star Wash / Deep Cleaning Included</span></li>
          <li className="flex items-center gap-2"><CalendarCheck className="h-[18px] w-[18px] shrink-0 text-[#0A66F0]" /> <span className="text-[13px] font-bold text-[#0E1A33]">One Plan Per Car, Monthly</span></li>
        </ul>

        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-auto inline-flex w-full items-center justify-center gap-2 rounded-[14px] bg-[#FFD21F] py-3 text-[14.5px] font-bold text-[#0E1A33] shadow-sm transition hover:brightness-95"
        >
          Request For Your Society <ArrowRight className="h-4 w-4" strokeWidth={2.5} />
        </button>
      </div>
      <div
        className="relative order-first h-[160px] w-full shrink-0 overflow-hidden [mask-image:linear-gradient(to_bottom,black_55%,transparent)] sm:absolute sm:inset-y-0 sm:right-0 sm:order-none sm:h-auto sm:w-[52%] sm:[mask-image:linear-gradient(to_right,transparent,black_48%)] lg:relative lg:h-[180px] lg:w-full lg:order-first lg:[mask-image:linear-gradient(to_bottom,black_55%,transparent)]"
      >
        <img
          src="/img/plans-card3-800.webp"
          alt=""
          width={800}
          height={450}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 h-full w-full object-cover object-top transition-transform duration-700 group-hover:scale-105"
        />
      </div>
      <SocietyRequestModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

const EMPTY = { society_name: "", area: "", pincode: "", contact_name: "", phone: "", approx_cars: "", note: "" };

export function SocietyRequestModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState("");
  const set = (patch: Partial<typeof EMPTY>) => setForm((f) => ({ ...f, ...patch }));
  const close = () => {
    onClose();
    if (sent) { setForm(EMPTY); setSent(""); }
    setError("");
  };

  const submit = async () => {
    const phone = validateIndianMobile(form.phone);
    const cars = Number(form.approx_cars);
    if (form.society_name.trim().length < 2) return setError("Enter the society's name.");
    if (form.area.trim().length < 2) return setError("Enter the area or locality.");
    if (!/^\d{6}$/.test(form.pincode)) return setError("Enter the 6-digit pincode.");
    if (form.contact_name.trim().length < 2) return setError("Enter your name.");
    if (!phone) return setError("Enter a valid 10-digit mobile number.");
    if (!Number.isInteger(cars) || cars < 1) return setError("Roughly how many cars? (a number)");
    setBusy(true);
    setError("");
    try {
      const message = await societyLeadApi.create({
        society_name: form.society_name.trim(), area: form.area.trim(), pincode: form.pincode, contact_name: form.contact_name.trim(),
        phone, approx_cars: cars, note: form.note.trim() || undefined,
      });
      trackLead("Society enquiry");
      setSent(message || "Thanks! Our team will call you within a day.");
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={close} title="Bring Blussit To Your Society">
      {sent ? (
        <div className="space-y-4 text-center" data-testid="society-request-sent">
          <CheckCircle2 className="mx-auto h-11 w-11 text-[#0A66F0]" />
          <p className="text-lg font-bold text-[#0E1A33]">Request Sent</p>
          <p className="text-sm text-[#5F6878]">{sent}</p>
          <button type="button" onClick={close} className="h-11 w-full rounded-[14px] bg-[#0A66F0] text-[15px] font-bold text-white hover:bg-[#0857CC]">Done</button>
        </div>
      ) : (
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <p className="text-sm text-[#5F6878]">Tell us about your society — we'll call to set up a visit and a price.</p>
          <div>
            <label className={fieldLabel} htmlFor="sr-name">Society Name</label>
            <input id="sr-name" className={field} value={form.society_name} maxLength={120} onChange={(e) => set({ society_name: e.target.value })} placeholder="e.g. Green Acres Phase 2" />
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
            <div>
              <label className={fieldLabel} htmlFor="sr-area">Area / Locality</label>
              <input id="sr-area" className={field} value={form.area} maxLength={120} onChange={(e) => set({ area: e.target.value })} placeholder="e.g. Vijay Nagar" />
            </div>
            <div>
              <label className={fieldLabel} htmlFor="sr-pin">Pincode</label>
              <input id="sr-pin" className={field} value={form.pincode} inputMode="numeric" onChange={(e) => set({ pincode: e.target.value.replace(/\D/g, "").slice(0, 6) })} placeholder="452010" />
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className={fieldLabel} htmlFor="sr-contact">Your Name</label>
              <input id="sr-contact" className={field} value={form.contact_name} maxLength={100} onChange={(e) => set({ contact_name: e.target.value })} placeholder="Secretary / resident" autoComplete="name" />
            </div>
            <div>
              <label className={fieldLabel} htmlFor="sr-phone">Mobile</label>
              <input id="sr-phone" className={field} value={form.phone} inputMode="tel" autoComplete="tel" onChange={(e) => set({ phone: e.target.value.replace(/[^\d+ ]/g, "").slice(0, 14) })} placeholder="10-digit number" />
            </div>
          </div>
          <div>
            <label className={fieldLabel} htmlFor="sr-cars">Approx. Number Of Cars</label>
            <input id="sr-cars" className={field} value={form.approx_cars} inputMode="numeric" onChange={(e) => set({ approx_cars: e.target.value.replace(/\D/g, "").slice(0, 4) })} placeholder="e.g. 60" />
          </div>
          <div>
            <label className={fieldLabel} htmlFor="sr-note">Note <span className="font-normal text-[#5F6878]">(Optional)</span></label>
            <textarea id="sr-note" rows={2} maxLength={500} value={form.note} onChange={(e) => set({ note: e.target.value })} placeholder="Towers, parking, best time to call"
              className="w-full rounded-[14px] border border-[#E4E9F1] bg-white px-4 py-3 text-[15px] text-[#0E1A33] outline-none placeholder:text-[#9AA3B2] focus:border-[#0A66F0] focus:ring-4 focus:ring-[#0A66F0]/10" />
          </div>
          {error && <p className="rounded-[12px] bg-red-50 px-3 py-2 text-sm text-[var(--color-error)]">{error}</p>}
          <button type="submit" disabled={busy}
            className="flex h-12 w-full items-center justify-center rounded-[14px] bg-[#FFD21F] text-[15px] font-bold text-[#0E1A33] transition hover:brightness-95 disabled:opacity-50">
            {busy ? "Sending…" : "Send Request"}
          </button>
        </form>
      )}
    </Modal>
  );
}
