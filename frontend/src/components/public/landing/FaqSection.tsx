import { useQuery } from "@tanstack/react-query";
import { Helmet } from "react-helmet-async";
import { contentApi } from "../../../api/catalog";
import { SectionShell, headingCase } from "./shared";

/**
 * Admin-managed FAQ section.
 * FAQ content is fetched from the public FAQ API and the same
 * content is used for the visible accordion and FAQPage JSON-LD.
 */

export function FaqSection({ id = "faq" }: { id?: string }) {
  const { data } = useQuery({
    queryKey: ["public-faqs"],
    queryFn: contentApi.faqs,
  });

  const faqs = data || [];

  if (!faqs.length) return null;

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((f) => ({
      "@type": "Question",
      name: f.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: f.answer,
      },
    })),
  };

  return (
    <SectionShell
      id={id}
      className="bg-[#F4F8FF] py-10 sm:py-12 lg:py-14"
    >
      <Helmet>
        <script type="application/ld+json">
          {JSON.stringify(jsonLd)}
        </script>
      </Helmet>

      {/* FAQ Heading */}
      <div className="mb-7 sm:mb-8">
        <h2 className="text-[32px] font-extrabold leading-[1.1] tracking-[-0.03em] text-[#071A3D] sm:text-[40px]">
          Frequently Asked{" "}
          <span className="relative inline-block text-[#1677FF]">
            Questions
            <svg viewBox="0 0 200 12" preserveAspectRatio="none" className="absolute -bottom-1.5 left-2 h-[8px] w-[75%]" aria-hidden="true">
              <path d="M2,8 C50,2 120,2 198,7" fill="none" stroke="#FACC15" strokeWidth="3" strokeLinecap="round" />
            </svg>
          </span>
        </h2>

        <p className="mt-4 text-[15px] leading-6 text-[#64748B] sm:text-[16px]">
          Everything customers usually ask before their first doorstep wash.
        </p>
      </div>

      {/* FAQ Accordion */}
      <div className="overflow-hidden rounded-[20px] border border-[#D9E8FF] bg-white shadow-sm">
        {faqs.map((f) => (
          <details
            key={f.id}
            className="
              group
              border-b
              border-[#D9E8FF]
              px-5
              py-4
              transition-colors
              last:border-b-0
              open:bg-[#F4F8FF]
              sm:px-6
            "
          >
            <summary
              className="
                flex
                cursor-pointer
                list-none
                items-center
                justify-between
                gap-4
                text-[15px]
                font-bold
                text-[#071A3D]
                transition-colors
                marker:content-none
                group-open:text-[#1677FF]
              "
            >
              {headingCase(f.question)}

              <span
                className="
                  shrink-0
                  text-lg
                  font-normal
                  text-[#1677FF]
                  transition-transform
                  duration-200
                  group-open:rotate-45
                "
              >
                +
              </span>
            </summary>

            <p className="mt-2.5 text-[14px] leading-relaxed text-[#64748B]">
              {f.answer}
            </p>
          </details>
        ))}
      </div>
    </SectionShell>
  );
}