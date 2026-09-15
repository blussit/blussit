import { useQuery } from "@tanstack/react-query";
import { Helmet } from "react-helmet-async";
import { contentApi } from "../../../api/catalog";
import { SectionHeader, SectionShell } from "./shared";

/**
 * The admin-managed FAQ list (already fully built on the backend — CRUD,
 * seed data — but never actually shown anywhere until now) rendered as a
 * plain <details>/<summary> accordion, plus matching FAQPage structured
 * data. Google explicitly requires FAQPage schema to match content that's
 * genuinely visible on the page — emitting the schema without a real
 * section here would be exactly the kind of mismatch that gets structured
 * data ignored (or penalized), so the visible section always comes first
 * and the JSON-LD is generated from the same fetched list, never written
 * by hand.
 */
export function FaqSection({ id = "faq" }: { id?: string }) {
  const { data } = useQuery({ queryKey: ["public-faqs"], queryFn: contentApi.faqs });
  const faqs = data || [];

  if (!faqs.length) return null;

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({
      "@type": "Question",
      name: f.question,
      acceptedAnswer: { "@type": "Answer", text: f.answer },
    })),
  };

  return (
    <SectionShell id={id} className="border-t border-cream-line-soft bg-white">
      <Helmet>
        <script type="application/ld+json">{JSON.stringify(jsonLd)}</script>
      </Helmet>
      <SectionHeader title="Frequently asked questions" subtitle="Everything customers usually ask before their first doorstep wash." />
      <div className="mt-8 divide-y divide-cream-line-soft rounded-2xl border border-cream-line bg-white sm:mt-10">
        {faqs.map((f) => (
          <details key={f.id} className="group px-5 py-4 open:bg-[#FFFCF0] sm:px-6">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[15px] font-semibold text-black marker:content-none">
              {f.question}
              <span className="shrink-0 text-lg font-normal text-neutral-400 transition-transform duration-200 group-open:rotate-45">+</span>
            </summary>
            <p className="mt-2.5 text-[14px] leading-relaxed text-neutral-600">{f.answer}</p>
          </details>
        ))}
      </div>
    </SectionShell>
  );
}
