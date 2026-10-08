import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { MessageCircle, Phone } from "lucide-react";
import { BUSINESS } from "../../seo/content";
import { BLUE, MUTED, PageHero, SeoPage } from "./seo/SeoKit";

/**
 * The four policy pages (service, cancellation, terms, privacy) share this
 * frame — same navbar, breadcrumb, hero and footer as the rest of the public
 * site. Keep every policy short and in plain words: a customer should get
 * the rule from the headings alone.
 */

const HELP_HREF = `https://wa.me/${import.meta.env.VITE_WHATSAPP_NUMBER || "918962288774"}?text=${encodeURIComponent("Hi Blussit, I have a question.")}`;

export function PolicyPage({ path, title, intro, updated, children }: { path: string; title: string; intro: string; updated: string; children: ReactNode }) {
  return (
    <SeoPage path={path} crumbs={[{ label: title, path }]}>
      <PageHero eyebrow="Policy" title={title} intro={intro} />
      <div className="container-page pb-14 md:pb-20">
        <div className="max-w-3xl space-y-4">{children}</div>
        <div className="mt-8 flex max-w-3xl flex-col gap-4 rounded-[18px] bg-[#F4F8FF] p-5 sm:flex-row sm:items-center sm:justify-between md:p-6">
          <div>
            <p className="font-display text-[17px] font-bold">Still Have A Question?</p>
            <p className="mt-0.5 text-[14px]" style={{ color: MUTED }}>
              Message us on WhatsApp or call {BUSINESS.phoneDisplay}.
            </p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <a
              href={HELP_HREF}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-11 items-center gap-2 rounded-full bg-[#0A66F0] px-5 text-[14px] font-bold text-white hover:bg-[#0857CF]"
            >
              <MessageCircle className="h-4 w-4" aria-hidden="true" /> WhatsApp Us
            </a>
            <a
              href={BUSINESS.phoneHref}
              className="inline-flex h-11 items-center gap-2 rounded-full border border-[#D6DEEA] bg-white px-5 text-[14px] font-semibold hover:border-[#0E1A33]"
            >
              <Phone className="h-4 w-4" aria-hidden="true" /> Call Us
            </a>
          </div>
        </div>
        <p className="mt-6 text-[13px]" style={{ color: MUTED }}>
          Last updated {updated}. We may update this page; the latest version is always the one shown here.
        </p>
      </div>
    </SeoPage>
  );
}

export function PolicyCard({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <section className="rounded-[18px] border border-[#E4EBF5] bg-white p-5 md:p-6">
      <div className="flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#EEF4FF]">
          <Icon className="h-[18px] w-[18px]" style={{ color: BLUE }} aria-hidden="true" />
        </span>
        <h2 className="font-display text-[18px] font-bold md:text-[19px]">{title}</h2>
      </div>
      <div className="mt-3 text-[15px] leading-[1.65]" style={{ color: MUTED }}>
        {children}
      </div>
    </section>
  );
}

export function Points({ items }: { items: ReactNode[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[9px] h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: BLUE }} aria-hidden="true" />
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}
