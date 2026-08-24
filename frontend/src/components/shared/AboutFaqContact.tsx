import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Mail, MapPin, Phone } from "lucide-react";
import { contentApi } from "../../api/catalog";
import { getErrorMessage } from "../../lib/api-client";
import { Section } from "./Section";
import { Button, Card, Input } from "../ui";

const fallbackFaqs = [
  { id: "1", question: "How do I book a car wash?", answer: "Tap Book Now, pick a slot, add your vehicle & address, and confirm." },
  { id: "2", question: "What areas do you serve?", answer: "We currently serve Indore, Madhya Pradesh, and are expanding to nearby cities." },
  { id: "3", question: "How much time does it take?", answer: "Most washes take 30-60 minutes depending on the service selected." },
  { id: "4", question: "What products do you use?", answer: "We use water-efficient, vehicle-safe cleaning products and equipment." },
  { id: "5", question: "Is water used in the wash?", answer: "Yes, minimal water with eco-friendly techniques to reduce wastage." },
  { id: "6", question: "Can I reschedule or cancel?", answer: "Yes, you can reschedule or cancel from your bookings page any time before the captain heads out." },
];

export function AboutFaqContact() {
  const { data } = useQuery({ queryKey: ["public-faqs"], queryFn: contentApi.faqs });
  const faqs = data?.length ? data : fallbackFaqs;
  const [openId, setOpenId] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", phone: "", email: "", message: "" });
  const [sent, setSent] = useState(false);
  const [sendError, setSendError] = useState("");
  const [sending, setSending] = useState(false);

  return (
    <Section id="contact" className="bg-[var(--color-surface)]">
      <div className="grid grid-cols-1 gap-10 lg:grid-cols-2">
        {/* About / FAQ */}
        <div>
          <h2 className="font-display text-2xl font-bold text-[var(--color-text-primary)]">About Us</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">Frequently asked questions</p>
          <div className="mt-6 divide-y divide-gray-200 rounded-2xl border border-gray-200 bg-white">
            {faqs.map((faq) => {
              const isOpen = openId === faq.id;
              return (
                <div key={faq.id} className="px-5">
                  <button
                    className="flex w-full items-center justify-between gap-4 py-4 text-left text-sm font-medium text-[var(--color-text-primary)]"
                    onClick={() => setOpenId(isOpen ? null : faq.id)}
                  >
                    {faq.question}
                    <ChevronDown className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? "rotate-180" : ""}`} />
                  </button>
                  {isOpen && <p className="pb-4 text-sm text-[var(--color-text-secondary)]">{faq.answer}</p>}
                </div>
              );
            })}
          </div>
        </div>

        {/* Contact */}
        <div>
          <h2 className="font-display text-2xl font-bold text-[var(--color-text-primary)]">Contact Us</h2>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">We're here to help</p>

          <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <Phone className="h-4 w-4 text-[var(--color-primary)]" /> +91 12345 67890
            </div>
            <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <Mail className="h-4 w-4 text-[var(--color-primary)]" /> hello@cleanride.in
            </div>
            <div className="flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <MapPin className="h-4 w-4 text-[var(--color-primary)]" /> Indore, MP
            </div>
          </div>

          <Card className="mt-6 p-6">
            {sent ? (
              <p className="py-6 text-center text-sm font-medium text-[var(--color-success)]">
                Thanks — we've received your message and will get back to you shortly.
              </p>
            ) : (
              <form
                className="space-y-4"
                onSubmit={async (e) => {
                  e.preventDefault();
                  setSendError("");
                  setSending(true);
                  try {
                    await contentApi.submitContactMessage(form);
                    setSent(true);
                  } catch (err) {
                    setSendError(getErrorMessage(err));
                  } finally {
                    setSending(false);
                  }
                }}
              >
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Input label="Your Name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
                  <Input label="Your Phone" value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} required />
                </div>
                <Input
                  label="Your Email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                  required
                />
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-[var(--color-text-primary)]">Your Message</label>
                  <textarea
                    className="w-full rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
                    rows={4}
                    value={form.message}
                    onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
                    required
                  />
                </div>
                <Button type="submit" className="w-full" isLoading={sending}>
                  Send Message
                </Button>
                {sendError && <p className="text-sm text-[var(--color-error)]">{sendError}</p>}
              </form>
            )}
          </Card>
        </div>
      </div>
    </Section>
  );
}
