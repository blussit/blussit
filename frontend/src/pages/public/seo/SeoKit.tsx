import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, MessageCircle } from "lucide-react";
import { PublicNavbar } from "../../../components/layout/PublicNavbar";
import { PublicFooter } from "../../../components/layout/PublicFooter";
import { PageSeo } from "../../../components/shared/PageSeo";
import { useAuth } from "../../../context/AuthContext";
import { INR } from "../../../components/public/landing/shared";
import seoConfig from "../../../seo/pages.json";
import type { Faq } from "../../../seo/content";

/**
 * Shared pieces of the search-landing pages. These pages are pre-rendered
 * to static HTML at build time (scripts/prerender.mjs), so everything here
 * must render the same on the server as in the browser: no window, no
 * storage, no effects that change the first render.
 */

export const NAVY = "#0E1A33";
export const BLUE = "#0A66F0";
export const MUTED = "#5F6878";
export const SITE = seoConfig.siteUrl;

const WHATSAPP_HREF = `https://wa.me/${import.meta.env.VITE_WHATSAPP_NUMBER || "918962288774"}?text=${encodeURIComponent("Hi Blussit, I would like to book a car wash.")}`;

export function JsonLd({ data }: { data: object }) {
  // "<" escaped so a string in the data can never close the script tag.
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }} />;
}

/** Signed-in customers book from their account; everyone else from /book. */
export function useBookHref(serviceSlug?: string) {
  const { user } = useAuth();
  const base = user?.role === "customer" ? "/app/book" : "/book";
  return serviceSlug ? `${base}?service=${encodeURIComponent(serviceSlug)}` : base;
}

export interface Crumb {
  label: string;
  path: string;
}

export function SeoPage({ path, crumbs, children }: { path: string; crumbs?: Crumb[]; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-white" style={{ color: NAVY }}>
      <PageSeo path={path} />
      <PublicNavbar />
      <main>
        {crumbs && <Breadcrumbs crumbs={crumbs} />}
        {children}
      </main>
      <PublicFooter />
    </div>
  );
}

function Breadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  const all = [{ label: "Home", path: "/" }, ...crumbs];
  return (
    <nav aria-label="Breadcrumb" className="container-page pt-5 md:pt-6">
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "BreadcrumbList",
          itemListElement: all.map((c, i) => ({ "@type": "ListItem", position: i + 1, name: c.label, item: `${SITE}${c.path}` })),
        }}
      />
      <ol className="flex flex-wrap items-center gap-1 text-[13px]" style={{ color: MUTED }}>
        {all.map((c, i) => (
          <li key={c.path} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />}
            {i === all.length - 1 ? (
              <span aria-current="page" className="font-semibold" style={{ color: NAVY }}>
                {c.label}
              </span>
            ) : (
              <Link to={c.path} className="hover:underline">
                {c.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHero({ eyebrow, title, intro, children }: { eyebrow?: string; title: string; intro: string; children?: ReactNode }) {
  return (
    <section className="container-page pb-8 pt-6 md:pb-12 md:pt-8">
      {eyebrow && (
        <p className="text-[13px] font-extrabold uppercase tracking-[0.1em]" style={{ color: BLUE }}>
          {eyebrow}
        </p>
      )}
      <h1 className="mt-2 max-w-3xl font-display text-[30px] font-extrabold leading-[1.1] tracking-[-0.02em] sm:text-[38px] lg:text-[46px]">{title}</h1>
      <p className="mt-4 max-w-2xl text-[15px] leading-[1.6] md:text-[17px]" style={{ color: MUTED }}>
        {intro}
      </p>
      {children}
    </section>
  );
}

export function BookButtons({ bookHref, label = "Book Now" }: { bookHref: string; label?: string }) {
  return (
    <div className="mt-6 flex flex-wrap gap-3">
      <Link to={bookHref} className="inline-flex h-12 items-center justify-center rounded-full bg-[#FFD21F] px-7 text-[15px] font-bold shadow-sm hover:bg-[#F5C400]" style={{ color: NAVY }}>
        {label}
      </Link>
      <a
        href={WHATSAPP_HREF}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex h-12 items-center justify-center gap-2 rounded-full border border-[#D6DEEA] bg-white px-6 text-[15px] font-semibold hover:border-[#0E1A33]"
      >
        <MessageCircle className="h-4 w-4" aria-hidden="true" /> Book On WhatsApp
      </a>
    </div>
  );
}

export function Section({ title, intro, children, tint = false, id }: { title: string; intro?: string; children: ReactNode; tint?: boolean; id?: string }) {
  return (
    <section id={id} className={tint ? "bg-[#F4F8FF] py-10 md:py-14" : "py-10 md:py-14"}>
      <div className="container-page">
        <h2 className="font-display text-[24px] font-extrabold leading-[1.15] tracking-[-0.02em] sm:text-[30px]">{title}</h2>
        {intro && (
          <p className="mt-2 max-w-2xl text-[15px] leading-[1.6]" style={{ color: MUTED }}>
            {intro}
          </p>
        )}
        <div className="mt-6">{children}</div>
      </div>
    </section>
  );
}

export function Price({ price, original, varies }: { price: number; original: number | null; varies: boolean }) {
  return (
    <span className="whitespace-nowrap">
      {varies && <span className="mr-1 text-[13px] font-semibold" style={{ color: MUTED }}>From</span>}
      <span className="font-mono-num text-[22px] font-extrabold">{INR(price)}</span>
      {original != null && original > price && <span className="ml-2 text-[14px] text-[#94A0B4] line-through">{INR(original)}</span>}
    </span>
  );
}

export function FaqList({ faqs }: { faqs: Faq[] }) {
  return (
    <>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "FAQPage",
          mainEntity: faqs.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })),
        }}
      />
      <div className="divide-y divide-[#E4EBF5] overflow-hidden rounded-[18px] border border-[#D9E8FF] bg-white">
        {faqs.map((f) => (
          <details key={f.q} className="group">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-[15px] font-bold md:text-[16px]">
              {f.q}
              <ChevronDown className="h-5 w-5 shrink-0 transition-transform group-open:rotate-180" style={{ color: BLUE }} aria-hidden="true" />
            </summary>
            <p className="px-5 pb-5 text-[14.5px] leading-[1.65]" style={{ color: MUTED }}>
              {f.a}
            </p>
          </details>
        ))}
      </div>
    </>
  );
}

export function CtaBand({ bookHref, title = "Book A Doorstep Wash In Indore", text = "Pick your wash and a time slot — a verified Blussit captain comes to you." }: { bookHref: string; title?: string; text?: string }) {
  return (
    <section className="py-10 md:py-14">
      <div className="container-page">
        <div className="rounded-[24px] px-6 py-9 text-white md:px-10 md:py-12" style={{ backgroundColor: NAVY }}>
          <h2 className="font-display text-[24px] font-extrabold leading-[1.15] sm:text-[30px]">{title}</h2>
          <p className="mt-2 max-w-xl text-[15px] leading-[1.6] text-white/75">{text}</p>
          <Link to={bookHref} className="mt-6 inline-flex h-12 items-center justify-center rounded-full bg-[#FFD21F] px-7 text-[15px] font-bold hover:bg-[#F5C400]" style={{ color: NAVY }}>
            Book Now
          </Link>
        </div>
      </div>
    </section>
  );
}
