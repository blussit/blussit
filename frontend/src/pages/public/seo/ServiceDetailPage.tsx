import { Link, useParams } from "react-router-dom";
import { Check, Clock, MapPin } from "lucide-react";
import { BUSINESS, HOW_IT_WORKS, serviceBySlug } from "../../../seo/content";
import { liveFor, usePublicServices } from "../../../seo/useLiveServices";
import { INR, serviceImageSrcSet } from "../../../components/public/landing/shared";
import { BLUE, BookButtons, CtaBand, FaqList, JsonLd, MUTED, PageHero, Price, SITE, SeoPage, Section, useBookHref } from "./SeoKit";
import NotFoundPage from "./NotFoundPage";

/** /services/:slug — one page per service, written for "<service> at home in Indore" searches. */
export default function ServiceDetailPage() {
  const { slug } = useParams();
  const all = usePublicServices();
  const content = serviceBySlug(slug);
  const live = content ? liveFor(content, all) : null;
  const bookHref = useBookHref(live?.bookSlug);
  if (!content || !live) return <NotFoundPage />;

  const path = `/services/${content.slug}`;
  const variants = (content.variantSlugs ?? []).flatMap((v) => all.find((s) => s.slug === v) ?? []);
  const related = content.related.flatMap((r) => serviceBySlug(r) ?? []);

  return (
    <SeoPage path={path} crumbs={[{ label: "Services", path: "/services" }, { label: content.name, path }]}>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Service",
          name: content.name,
          serviceType: content.vehicle === "bike" ? "Doorstep bike wash" : "Doorstep car wash",
          description: content.tagline,
          url: `${SITE}${path}`,
          image: `${SITE}${content.image.src}`,
          provider: { "@id": `${SITE}/#organization` },
          areaServed: { "@type": "City", name: BUSINESS.city },
          offers: {
            "@type": "Offer",
            price: live.price,
            priceCurrency: "INR",
            availability: "https://schema.org/InStock",
            url: `${SITE}/book?service=${encodeURIComponent(live.bookSlug)}`,
          },
        }}
      />
      <div className="container-page grid items-start gap-8 lg:grid-cols-[1.1fr_1fr] lg:gap-12">
        <PageHero eyebrow={live.offerTag || `Doorstep ${content.vehicle === "bike" ? "Bike" : "Car"} Care · Indore`} title={content.h1} intro={content.tagline}>
          <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2">
            <Price price={live.price} original={live.original} varies={live.varies} />
            <span className="flex items-center gap-1.5 text-[14px]" style={{ color: MUTED }}>
              <Clock className="h-4 w-4" aria-hidden="true" /> About {live.minutes} Mins
            </span>
          </div>
          {variants.map((v) => (
            <p key={v.id} className="mt-2 text-[14px]" style={{ color: MUTED }}>
              {v.variant_label ? `${v.variant_label}: ` : `${v.name}: `}
              <span className="font-bold" style={{ color: "#0E1A33" }}>{INR(v.price)}</span>
            </p>
          ))}
          {live.varies && <p className="mt-2 text-[13px]" style={{ color: MUTED }}>The exact price for your car size is shown while booking.</p>}
          <BookButtons bookHref={bookHref} label={`Book ${content.name}`} />
        </PageHero>
        <div className="overflow-hidden rounded-[20px] bg-[#F3F6FA] lg:mt-8">
          {/* The page's largest paint — fetched first, at the size it's shown. */}
          <img
            src={content.image.src}
            srcSet={serviceImageSrcSet(content.image.src)}
            sizes="(min-width: 1024px) 600px, 100vw"
            fetchPriority="high"
            width={content.image.width}
            height={content.image.height}
            alt={`${content.name} at home by Blussit in Indore`}
            className="h-auto w-full object-cover"
          />
        </div>
      </div>

      <Section title={`What's Included In ${content.name}`} tint>
        <div className="grid gap-8 lg:grid-cols-2">
          <ul className="space-y-3">
            {live.includes.map((item) => (
              <li key={item} className="flex items-start gap-3 text-[15.5px] font-semibold">
                <Check className="mt-0.5 h-5 w-5 shrink-0" style={{ color: BLUE }} strokeWidth={3} aria-hidden="true" /> {item}
              </li>
            ))}
          </ul>
          <div className="space-y-4 text-[15px] leading-[1.7]" style={{ color: MUTED }}>
            {content.intro.map((p) => (
              <p key={p.slice(0, 32)}>{p}</p>
            ))}
          </div>
        </div>
      </Section>

      <Section title="Best For">
        <ul className="grid gap-3 sm:grid-cols-3">
          {content.bestFor.map((b) => (
            <li key={b} className="rounded-[16px] border border-[#EDF0F5] p-4 text-[15px] font-semibold shadow-[0_6px_20px_rgba(15,30,60,0.05)]">
              {b}
            </li>
          ))}
        </ul>
        <p className="mt-6 max-w-3xl text-[15px] leading-[1.7]" style={{ color: MUTED }}>
          <span className="font-bold" style={{ color: "#0E1A33" }}>What To Keep Ready: </span>
          {content.keepReady}
        </p>
      </Section>

      <Section title="How It Works" tint>
        <ol className="grid gap-4 md:grid-cols-3">
          {HOW_IT_WORKS.map((step, i) => (
            <li key={step.title} className="rounded-[18px] bg-white p-5 shadow-[0_6px_20px_rgba(15,30,60,0.05)]">
              <span className="font-mono-num text-[13px] font-extrabold" style={{ color: BLUE }}>
                Step {i + 1}
              </span>
              <p className="mt-1 text-[17px] font-bold">{step.title}</p>
              <p className="mt-1.5 text-[14.5px] leading-[1.6]" style={{ color: MUTED }}>
                {step.text}
              </p>
            </li>
          ))}
        </ol>
        <p className="mt-6 flex items-center gap-2 text-[14.5px]" style={{ color: MUTED }}>
          <MapPin className="h-4 w-4 shrink-0" style={{ color: BLUE }} aria-hidden="true" />
          Available across Indore, {BUSINESS.hours}.{" "}
          <Link to="/doorstep-car-wash-indore" className="font-semibold underline" style={{ color: BLUE }}>
            See Areas We Cover
          </Link>
        </p>
      </Section>

      <Section title={`${content.name} FAQs`}>
        <FaqList faqs={content.faqs} />
      </Section>

      {related.length > 0 && (
        <Section title="Other Services" tint>
          <ul className="grid gap-3 sm:grid-cols-3">
            {related.map((r) => (
              <li key={r.slug}>
                <Link to={`/services/${r.slug}`} className="block h-full rounded-[16px] border border-[#EDF0F5] bg-white p-4 hover:border-[#0A66F0]">
                  <span className="text-[16px] font-bold">{r.name}</span>
                  <span className="mt-1 block text-[14px] leading-[1.5]" style={{ color: MUTED }}>
                    {r.tagline}
                  </span>
                </Link>
              </li>
            ))}
            <li>
              <Link to="/services" className="flex h-full items-center rounded-[16px] border border-dashed border-[#C9D5E6] p-4 text-[15px] font-bold" style={{ color: BLUE }}>
                All Services &amp; Prices
              </Link>
            </li>
          </ul>
        </Section>
      )}

      <CtaBand bookHref={bookHref} title={`Book ${content.name} In Indore`} />
    </SeoPage>
  );
}
