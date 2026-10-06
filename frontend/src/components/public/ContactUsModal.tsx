import { useEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, MessageCircle, X } from "lucide-react";
import { AnimatePresence, motion } from "framer-motion";
import { contentApi } from "../../api/catalog";
import { getErrorMessage } from "../../lib/api-client";
import { openBlussitWhatsApp } from "./WhatsAppFloatingButton";
import { useBodyScrollLock } from "../../hooks/useBodyScrollLock";

type ContactForm = {
  name: string;
  phone: string;
  email: string;
  vehicle_type: string;
  topic: string;
  message: string;
  consent: boolean;
};

type Errors = Partial<Record<keyof ContactForm, string>>;

const initialForm: ContactForm = {
  name: "",
  phone: "",
  email: "",
  vehicle_type: "",
  topic: "",
  message: "",
  consent: false,
};

const vehicleTypes = [
  "Hatchback",
  "Sedan",
  "SUV",
  "XUV 5-Seater",
  "XUV 7-Seater",
  "Bike",
  "Other",
];

const topics = [
  "General Enquiry",
  "Book a Service",
  "Service Information",
  "Pricing",
  "Corporate / Bulk Enquiry",
  "Partnership",
  "Complaint / Feedback",
  "Other",
];

function validate(form: ContactForm): Errors {
  const errors: Errors = {};

  if (form.name.trim().length < 2) {
    errors.name = "Please enter your full name.";
  }

  const phone = form.phone.replace(/[\s-]/g, "").replace(/^\+91/, "");

  if (!/^[6-9]\d{9}$/.test(phone)) {
    errors.phone = "Enter a valid 10-digit Indian phone number.";
  }

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
    errors.email = "Enter a valid email address.";
  }

  if (!form.topic) {
    errors.topic = "Please select a topic.";
  }

  if (form.message.trim().length < 10) {
    errors.message = "Please enter at least 10 characters.";
  }

  if (!form.consent) {
    errors.consent = "Please confirm that we may contact you.";
  }

  return errors;
}

export function ContactUsModal({
  open,
  onClose,
  returnFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  const [form, setForm] = useState<ContactForm>(initialForm);
  const [errors, setErrors] = useState<Errors>({});
  const [state, setState] = useState<
    "idle" | "submitting" | "success" | "error"
  >("idle");
  const [submitError, setSubmitError] = useState("");

  const nameRef = useRef<HTMLInputElement>(null);

  useBodyScrollLock(open);

  useEffect(() => {
    if (!open) return;

    const focusTimer = window.setTimeout(() => {
      nameRef.current?.focus();
    }, 50);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", onKeyDown);

    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", onKeyDown);

      window.setTimeout(() => {
        returnFocusRef?.current?.focus();
      }, 0);
    };
  }, [open, onClose, returnFocusRef]);

  const update = <K extends keyof ContactForm>(
    key: K,
    value: ContactForm[K],
  ) => {
    setForm((current) => ({
      ...current,
      [key]: value,
    }));

    setErrors((current) => ({
      ...current,
      [key]: undefined,
    }));

    if (state === "error") {
      setState("idle");
    }
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
        name: form.name.trim(),
        phone: form.phone.replace(/[\s-]/g, ""),
        email: form.email.trim(),
        message: [
          form.topic && `[${form.topic}]`,
          form.message.trim(),
          form.vehicle_type && `(Vehicle: ${form.vehicle_type})`,
        ]
          .filter(Boolean)
          .join(" "),
      });

      setState("success");
    } catch (error) {
      setState("error");
      setSubmitError(getErrorMessage(error));
    }
  };

  /*
   * BLUSSIT THEME
   * Navy  : #071A3D
   * Blue  : #1677FF
   * Yellow: #E8A900
   */

  const inputClass =
    "mt-1 block h-[46px] w-full rounded-[10px] border border-[#D9E4F2] bg-white px-3.5 text-sm text-[#071A3D] placeholder:text-[#91A0B5] outline-none transition-all focus:border-[#1677FF] focus:ring-2 focus:ring-[#1677FF]/10 sm:h-[48px]";

  const labelClass =
    "block text-[13px] font-semibold text-[#071A3D]";

  const error = (message?: string) =>
    message && (
      <p className="mt-1 text-xs text-red-600">
        {message}
      </p>
    );

  return createPortal(
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-[100000] flex items-center justify-center p-3 sm:p-5"
          role="presentation"
        >
          {/* BACKDROP */}
          <motion.button
            type="button"
            aria-label="Close contact form"
            className="absolute inset-0 cursor-default bg-[#071A3D]/45 backdrop-blur-[4px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          {/* MODAL */}
          <motion.section
            role="dialog"
            aria-modal="true"
            aria-labelledby="contact-modal-title"
            initial={{ opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="
              relative
              w-full
              max-w-[620px]
              overflow-hidden
              rounded-[20px]
              border
              border-[#DCE8F7]
              bg-white
              p-[18px]
              shadow-[0_24px_70px_rgba(7,26,61,0.20)]
              sm:p-6
            "
          >
            {/* SMALL YELLOW ACCENT — subtle, not a full top border */}
            <span
              className="
                pointer-events-none
                absolute
                left-6
                top-0
                h-[3px]
                w-12
                rounded-b-full
                bg-[#E8A900]
              "
            />

            {/* CLOSE */}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close Contact Us modal"
              className="
                absolute
                right-4
                top-4
                z-20
                flex
                h-9
                w-9
                items-center
                justify-center
                rounded-full
                border
                border-[#DCE8F7]
                bg-white
                text-[#1677FF]
                shadow-[0_4px_14px_rgba(7,26,61,0.08)]
                transition
                hover:border-[#1677FF]
                hover:bg-[#F2F7FF]
              "
            >
              <X className="h-[17px] w-[17px]" />
            </button>

            {state === "success" ? (
              <div className="flex min-h-[350px] flex-col items-center justify-center text-center">
                <span
                  className="
                    flex
                    h-14
                    w-14
                    items-center
                    justify-center
                    rounded-full
                    bg-[#FFF6D8]
                    text-[#E8A900]
                  "
                >
                  <Check
                    className="h-7 w-7"
                    strokeWidth={3}
                  />
                </span>

                <h2
                  id="contact-modal-title"
                  className="mt-5 text-2xl font-bold text-[#071A3D]"
                >
                  Message Sent Successfully
                </h2>

                <p className="mt-2 max-w-sm text-sm leading-6 text-[#64748B]">
                  Thanks for reaching out to Blussit. Our team will get back
                  to you shortly.
                </p>

                <button
                  type="button"
                  onClick={onClose}
                  className="
                    mt-6
                    h-11
                    rounded-[10px]
                    bg-[#E8A900]
                    px-7
                    text-sm
                    font-bold
                    text-[#071A3D]
                    shadow-[0_7px_18px_rgba(232,169,0,0.20)]
                    transition
                    hover:bg-[#D99D00]
                  "
                >
                  Done
                </button>
              </div>
            ) : (
              <>
                {/* HEADER */}
                <div className="pr-10">
                  <div className="flex items-center gap-2">
                    <p className="text-[11px] font-bold tracking-[0.16em] text-[#1677FF]">
                      GET IN TOUCH
                    </p>

                    <span className="h-1.5 w-1.5 rounded-full bg-[#E8A900]" />
                  </div>

                  <h2
                    id="contact-modal-title"
                    className="
                      mt-1.5
                      text-[24px]
                      font-bold
                      leading-tight
                      tracking-[-0.02em]
                      text-[#071A3D]
                      sm:text-[26px]
                    "
                  >
                    We&apos;re here to help.
                  </h2>

                  <p className="mt-1 text-[13px] leading-5 text-[#64748B]">
                    Tell us what you need and we&apos;ll get back to you
                    shortly.
                  </p>
                </div>

                {/* FORM */}
                <form
                  className="mt-4 space-y-3"
                  onSubmit={submit}
                  noValidate
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    {/* NAME */}
                    <label className={labelClass}>
                      Your Name{" "}
                      <span className="text-[#E8A900]">*</span>

                      <input
                        ref={nameRef}
                        value={form.name}
                        onChange={(e) =>
                          update("name", e.target.value)
                        }
                        autoComplete="name"
                        className={inputClass}
                        placeholder="Enter your name"
                      />

                      {error(errors.name)}
                    </label>

                    {/* PHONE */}
                    <label className={labelClass}>
                      WhatsApp / Phone{" "}
                      <span className="text-[#E8A900]">*</span>

                      <input
                        value={form.phone}
                        onChange={(e) =>
                          update("phone", e.target.value)
                        }
                        inputMode="tel"
                        autoComplete="tel"
                        className={inputClass}
                        placeholder="+91 98765 43210"
                      />

                      {error(errors.phone)}
                    </label>

                    {/* EMAIL */}
                    <label className={labelClass}>
                      Email Address{" "}
                      <span className="text-[#E8A900]">*</span>

                      <input
                        value={form.email}
                        onChange={(e) =>
                          update("email", e.target.value)
                        }
                        type="email"
                        autoComplete="email"
                        className={inputClass}
                        placeholder="email@example.com"
                      />

                      {error(errors.email)}
                    </label>

                    {/* VEHICLE */}
                    <label className={labelClass}>
                      Vehicle Type

                      <select
                        value={form.vehicle_type}
                        onChange={(e) =>
                          update("vehicle_type", e.target.value)
                        }
                        className={inputClass}
                      >
                        <option value="">Select vehicle</option>

                        {vehicleTypes.map((option) => (
                          <option
                            key={option}
                            value={option}
                          >
                            {option}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  {/* TOPIC */}
                  <label className={labelClass}>
                    How can we help?{" "}
                    <span className="text-[#E8A900]">*</span>

                    <select
                      value={form.topic}
                      onChange={(e) =>
                        update("topic", e.target.value)
                      }
                      className={inputClass}
                    >
                      <option value="">Select a topic</option>

                      {topics.map((option) => (
                        <option
                          key={option}
                          value={option}
                        >
                          {option}
                        </option>
                      ))}
                    </select>

                    {error(errors.topic)}
                  </label>

                  {/* MESSAGE */}
                  <label className={labelClass}>
                    Message{" "}
                    <span className="text-[#E8A900]">*</span>

                    <textarea
                      value={form.message}
                      onChange={(e) =>
                        update("message", e.target.value)
                      }
                      rows={3}
                      className={`${inputClass} h-[74px] resize-none py-2.5`}
                      placeholder="Tell us how we can help..."
                    />

                    {error(errors.message)}
                  </label>

                  {/* CONSENT */}
                  <label
                    className="
                      flex
                      cursor-pointer
                      items-start
                      gap-2.5
                      text-[13px]
                      text-[#071A3D]
                    "
                  >
                    <input
                      checked={form.consent}
                      onChange={(e) =>
                        update("consent", e.target.checked)
                      }
                      type="checkbox"
                      className="
                        mt-0.5
                        h-4
                        w-4
                        accent-[#E8A900]
                      "
                    />

                    <span>
                      I agree to be contacted by Blussit
                    </span>
                  </label>

                  {error(errors.consent)}

                  {/* API ERROR */}
                  {submitError && (
                    <p
                      className="
                        rounded-[9px]
                        border
                        border-red-200
                        bg-red-50
                        px-3
                        py-2
                        text-sm
                        text-red-700
                      "
                    >
                      {submitError}
                    </p>
                  )}

                  {/* ACTIONS */}
                  <div
                    className="
                      flex
                      flex-col-reverse
                      gap-3
                      pt-1
                      sm:flex-row
                      sm:items-center
                      sm:justify-between
                    "
                  >
                    {/* WHATSAPP */}
                    <button
                      type="button"
                      onClick={openBlussitWhatsApp}
                      className="
                        flex
                        items-center
                        justify-center
                        gap-1.5
                        text-[13px]
                        font-medium
                        text-[#64748B]
                        transition
                        hover:text-[#1677FF]
                      "
                    >
                      <MessageCircle className="h-4 w-4 text-[#1677FF]" />

                      <span>
                        Prefer WhatsApp?{" "}
                        <span className="font-semibold text-[#1677FF]">
                          Chat with us →
                        </span>
                      </span>
                    </button>

                    {/* SUBMIT */}
                    <button
                      type="submit"
                      disabled={state === "submitting"}
                      className="
                        h-[46px]
                        w-full
                        shrink-0
                        rounded-[10px]
                        bg-[#E8A900]
                        px-5
                        text-sm
                        font-bold
                        text-[#071A3D]
                        shadow-[0_7px_18px_rgba(232,169,0,0.18)]
                        transition-all
                        hover:-translate-y-0.5
                        hover:bg-[#D99D00]
                        disabled:cursor-not-allowed
                        disabled:opacity-70
                        sm:h-12
                        sm:w-[205px]
                      "
                    >
                      {state === "submitting"
                        ? "Sending..."
                        : state === "error"
                          ? "Try Again"
                          : "Send Message →"}
                    </button>
                  </div>
                </form>
              </>
            )}
          </motion.section>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
