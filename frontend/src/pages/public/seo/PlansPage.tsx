import { Link } from "react-router-dom";
import { PlansShowcase } from "../../../components/public/landing/PlansShowcase";
import { PLANS_FAQS } from "../../../seo/content";
import { BLUE, CtaBand, FaqList, MUTED, PageHero, SeoPage, Section, useBookHref } from "./SeoKit";

/** /plans — the landing's plans section on a page of its own, for "monthly car wash plan" searches. */
export default function PlansPage() {
  const bookHref = useBookHref();
  return (
    <SeoPage path="/plans" crumbs={[{ label: "Plans", path: "/plans" }]}>
      <PageHero
        eyebrow="Monthly Plans"
        title="Monthly Car Wash Plans In Indore"
        intro="Keep your car clean all month without booking and paying for every wash. A Monthly Pass covers one car with a set number of doorstep washes; Society Plans bring a dedicated captain to your society every morning; and fleets get a custom plan."
      />
      <PlansShowcase />
      <Section title="Plan Questions" tint>
        <FaqList faqs={PLANS_FAQS} />
        <p className="mt-5 text-[15px]" style={{ color: MUTED }}>
          Want a single wash instead?{" "}
          <Link to="/services" className="font-semibold underline" style={{ color: BLUE }}>
            See All Services And Prices
          </Link>
          .
        </p>
      </Section>
      <CtaBand bookHref={bookHref} />
    </SeoPage>
  );
}
