import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, MessageCircle, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { contentApi } from "../../api/catalog";
import { getErrorMessage } from "../../lib/api-client";
import { openBlussitWhatsApp } from "./WhatsAppFloatingButton";

type ContactForm = { name: string; phone: string; email: string; vehicle_type: string; topic: string; message: string; consent: boolean };
type Errors = Partial<Record<keyof ContactForm, string>>;

const initialForm: ContactForm = { name: "", phone: "", email: "", vehicle_type: "", topic: "", message: "", consent: false };
const vehicleTypes = ["Hatchback", "Sedan", "SUV", "XUV 5-Seater", "XUV 7-Seater", "Bike", "Other"];
const topics = ["General Enquiry", "Book a Service", "Service Information", "Pricing", "Corporate / Bulk Enquiry", "Partnership", "Complaint / Feedback", "Other"];

function validate(form: ContactForm): Errors {
  const errors: Errors = {};
  if (form.name.trim().length < 2) errors.name = "Please enter your full name.";
  const phone = form.phone.replace(/[\s-]/g, "").replace(/^\+91/, "");
  if (!/^[6-9]\d{9}$/.test(phone)) errors.phone = "Enter a valid 10-digit Indian phone number.";
  if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = "Enter a valid email address.";
  if (!form.topic) errors.topic = "Please select a topic.";
  if (form.message.trim().length < 10) errors.message = "Please enter at least 10 characters.";
  if (!form.consent) errors.consent = "Please confirm that we may contact you.";
  return errors;
}

export function ContactUsModal({ open, onClose, returnFocusRef }: { open: boolean; onClose: () => void; returnFocusRef?: RefObject<HTMLElement | null> }) {
  const [form, setForm] = useState<ContactForm>(initialForm);
  const [errors, setErrors] = useState<Errors>({});
  const [state, setState] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const [submitError, setSubmitError] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => nameRef.current?.focus(), 50);
    const onKeyDown = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKeyDown);
      window.setTimeout(() => returnFocusRef?.current?.focus(), 0);
    };
  }, [open, onClose, returnFocusRef]);

  const update = <K extends keyof ContactForm>(key: K, value: ContactForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
    if (state === "error") setState("idle");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (state === "submitting") return;
    const nextErrors = validate(form);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    setState("submitting");
    setSubmitError("");
    try {
      await contentApi.submitContactMessage({
        name: form.name.trim(), phone: form.phone.replace(/[\s-]/g, ""), email: form.email.trim() || undefined,
        vehicle_type: form.vehicle_type || undefined, topic: form.topic, message: form.message.trim(),
      });
      setState("success");
    } catch (error) {
      setState("error");
      setSubmitError(getErrorMessage(error));
    }
  };

  const inputClass = "mt-1 block h-[46px] w-full rounded-[11px] border border-[#e1e1e1] bg-[#fafaf9] px-3 text-sm text-[#222] placeholder:text-[#929292] outline-none transition focus:border-[#E8A900] focus:bg-white focus:ring-2 focus:ring-[#E8A900]/15 sm:h-12";
  const labelClass = "block text-[13px] font-semibold text-[#222]";
  const error = (message?: string) => message && <p className="mt-1 text-xs text-red-600">{message}</p>;

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-[100000] flex items-center justify-center p-3 sm:p-5" role="presentation">
          <motion.button type="button" aria-label="Close contact form" className="absolute inset-0 cursor-default bg-black/45 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose} />
          <motion.section role="dialog" aria-modal="true" aria-labelledby="contact-modal-title" initial={{ opacity: 0, y: 8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 8, scale: 0.98 }} transition={{ duration: 0.2, ease: "easeOut" }} className="relative w-full max-w-[660px] rounded-[22px] border border-[#e7e4dd] bg-[#fffefd] p-[18px] shadow-[0_20px_60px_rgba(25,22,17,0.20)] sm:p-6">
            <button type="button" onClick={onClose} aria-label="Close Contact Us modal" className="absolute right-3 top-3 rounded-full border border-[#e8e5de] bg-[#faf9f6] p-2 text-[#333] transition hover:border-[#E8A900]/50 hover:bg-[#fff4cd] focus-visible:outline-[#E8A900]"><X className="h-4 w-4" /></button>
            {state === "success" ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                <span className="flex h-14 w-14 items-center justify-center rounded-full bg-[#E8A900] text-white"><Check className="h-8 w-8" strokeWidth={3} /></span>
                <h2 id="contact-modal-title" className="mt-5 text-2xl font-bold text-[#171717]">Message Sent Successfully</h2>
                <p className="mt-2 max-w-sm text-sm leading-6 text-[#666]">Thanks for reaching out to Blussit. Our team will get back to you shortly.</p>
                <button type="button" onClick={onClose} className="mt-6 h-12 rounded-[11px] bg-[#E8A900] px-7 text-sm font-bold text-white transition hover:bg-[#d99a00]">Done</button>
              </div>
            ) : (
              <>
                <p className="text-xs font-bold tracking-[0.18em] text-[#222] after:ml-2 after:inline-block after:h-px after:w-7 after:align-middle after:bg-[#E8A900] after:content-['']">GET IN TOUCH</p>
                <h2 id="contact-modal-title" className="mt-1.5 pr-9 text-[24px] font-bold leading-tight text-[#171717] sm:text-[26px]">We&apos;re here to help.</h2>
                <p className="mt-1 text-[13px] text-[#6b6b6b]">Tell us what you need and we&apos;ll get back to you shortly.</p>
                <form className="mt-3 space-y-2 sm:mt-4 sm:space-y-3" onSubmit={submit} noValidate>
                  <div className="grid gap-2 sm:grid-cols-2 sm:gap-3">
                    <label className={labelClass}>Your Name <span className="text-[#D99A00]">*</span><input ref={nameRef} value={form.name} onChange={(e) => update("name", e.target.value)} autoComplete="name" className={inputClass} placeholder="Enter your name" />{error(errors.name)}</label>
                    <label className={labelClass}>WhatsApp / Phone <span className="text-[#D99A00]">*</span><input value={form.phone} onChange={(e) => update("phone", e.target.value)} inputMode="tel" autoComplete="tel" className={inputClass} placeholder="+91 98765 43210" />{error(errors.phone)}</label>
                    <label className={labelClass}>Email Address <span className="font-medium text-[#888]">(Optional)</span><input value={form.email} onChange={(e) => update("email", e.target.value)} type="email" autoComplete="email" className={inputClass} placeholder="email@example.com" />{error(errors.email)}</label>
                    <label className={labelClass}>Vehicle Type<select value={form.vehicle_type} onChange={(e) => update("vehicle_type", e.target.value)} className={inputClass}><option value="" className="text-black">Select vehicle</option>{vehicleTypes.map((option) => <option key={option} value={option} className="text-black">{option}</option>)}</select></label>
                  </div>
                  <label className={labelClass}>How can we help? <span className="text-[#D99A00]">*</span><select value={form.topic} onChange={(e) => update("topic", e.target.value)} className={inputClass}><option value="" className="text-black">Select a topic</option>{topics.map((option) => <option key={option} value={option} className="text-black">{option}</option>)}</select>{error(errors.topic)}</label>
                  <label className={labelClass}>Message <span className="text-[#D99A00]">*</span><textarea value={form.message} onChange={(e) => update("message", e.target.value)} rows={3} className={`${inputClass} h-[72px] resize-none py-2 sm:h-[78px] sm:py-2.5`} placeholder="Tell us how we can help..." />{error(errors.message)}</label>
                  <label className="flex cursor-pointer items-start gap-2.5 text-[13px] text-[#555]"><input checked={form.consent} onChange={(e) => update("consent", e.target.checked)} type="checkbox" className="mt-0.5 h-4 w-4 accent-[#E8A900]" /><span>I agree to be contacted by Blussit</span></label>
                  {error(errors.consent)}
                  {submitError && <p className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</p>}
                  <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                    <button type="button" onClick={openBlussitWhatsApp} className="flex items-center justify-center gap-1.5 text-[13px] font-medium text-[#666] transition hover:text-[#9a6a00]"><MessageCircle className="h-4 w-4 text-[#E8A900]" /> Prefer WhatsApp? <span className="text-[#9a6a00]">Chat with us →</span></button>
                    <button type="submit" disabled={state === "submitting"} className="h-[46px] w-full shrink-0 rounded-[11px] bg-[#E8A900] px-5 text-sm font-bold text-white transition hover:bg-[#d99a00] disabled:cursor-not-allowed disabled:opacity-70 sm:h-12 sm:w-[220px]">{state === "submitting" ? "Sending..." : state === "error" ? "Try Again" : "Send Message →"}</button>
                  </div>
                </form>
              </>
            )}
          </motion.section>
        </div>
      )}
    </AnimatePresence>, document.body,
  );
}
