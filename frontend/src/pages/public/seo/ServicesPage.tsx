import { Link } from "react-router-dom";
import { Check, Clock } from "lucide-react";
import { SERVICES } from "../../../seo/content";
import { liveFor, usePublicServices } from "../../../seo/useLiveServices";
import { FAQS } from "../../../data/faqs";
import { INR, serviceImageSrcSet, titleCase } from "../../../components/public/landing/shared";
import { BLUE, BookButtons, CtaBand, FaqList, JsonLd, MUTED, PageHero, Price, SITE, SeoPage, Section, useBookHref } from "./SeoKit";

const PICKER = [
  { need: "A quick clean outside", pick: "jet-wash" },
  { need: "Clean inside and out, every week or two", pick: "star-wash" },
  { need: "No water tap where the car is parked", pick: "waterless-car-wash" },
  { need: "Dirty seats, mats or floor", pick: "car-deep-cleaning" },
  { need: "A bike or scooty", pick: "bike-wash" },
];

/** /services — every service, what it includes and what it costs. */
export default function ServicesPage() {
  const all = usePublicServices();
  const bookHref = useBookHref();
  const services = SERVICES.map((content) => ({ content, live: liveFor(content, all) }));
  const addons = all.filter((s) => s.is_addon);
  const faqs = FAQS.filter((f) => ["keep-ready", "payment", "cancel", "areas"].includes(f.id)).map((f) => ({ q: f.question, a: f.answer }));

  return (
    <SeoPage path="/services" crumbs={[{ label: "Services", path: "/services" }]}>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "ItemList",
          name: "Blussit doorstep car and bike wash services in Indore",
          itemListElement: services.map(({ content }, i) => ({
            "@type": "ListItem",
            position: i + 1,
            url: `${SITE}/services/${content.slug}`,
            name: content.name,
          })),
        }}
      />
      <PageHero
        eyebrow="Services & Prices"
        title="Car Wash Services At Your Doorstep In Indore"
        intro="Every Blussit service is done where your car or bike is parked — at home, at the office or in your society. Pick the wash that fits, see exactly what's included, and book a slot in a minute."
      >
        <BookButtons bookHref={bookHref} />
      </PageHero>

      <Section title="Choose Your Wash" tint>
        <ul className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {services.map(({ content, live }, index) => (
            <li key={content.slug} className="flex flex-col overflow-hidden rounded-[18px] border border-[#EDF0F5] bg-white shadow-[0_6px_20px_rgba(15,30,60,0.06)]">
              <img
                src={content.image.src}
                srcSet={serviceImageSrcSet(content.image.src)}
                sizes="(min-width: 1024px) 400px, (min-width: 640px) 50vw, 100vw"
                width={content.image.width}
                height={content.image.height}
                alt={`${content.name} by Blussit`}
                // The first card's photo is this page's largest paint on a phone.
                loading={index === 0 ? "eager" : "lazy"}
                fetchPriority={index === 0 ? "high" : undefined}
                className="aspect-[16/9] w-full object-cover"
              />
              <div className="flex flex-1 flex-col p-5">
                <h3 className="text-[19px] font-bold">
                  <Link to={`/services/${content.slug}`} className="hover:underline">
                    {content.name}
                  </Link>
                </h3>
                <p className="mt-1 text-[14px] leading-[1.55]" style={{ color: MUTED }}>
                  {content.tagline}
                </p>
                <ul className="mt-3 space-y-1.5">
                  {live.includes.map((item) => (
                    <li key={item} className="flex items-start gap-2 text-[14px]">
                      <Check className="mt-0.5 h-4 w-4 shrink-0" style={{ color: BLUE }} strokeWidth={3} aria-hidden="true" /> {item}
                    </li>
                  ))}
                </ul>
                <p className="mt-3 flex items-center gap-1.5 text-[13px]" style={{ color: MUTED }}>
                  <Clock className="h-4 w-4" aria-hidden="true" /> About {live.minutes} Mins
                </p>
                <div className="mt-auto flex items-end justify-between gap-3 pt-4">
                  <Price price={live.price} original={live.original} varies={live.varies} />
                  <Link to={`/services/${content.slug}`} className="text-[14px] font-bold" style={{ color: BLUE }}>
                    View Details
                  </Link>
                </div>
              </div>
            </li>
          ))}
        </ul>
        {addons.length > 0 && (
          <div className="mt-8 rounded-[18px] border border-[#D9E8FF] bg-white p-5">
            <h3 className="text-[17px] font-bold">Add-Ons</h3>
            <p className="mt-1 text-[14px]" style={{ color: MUTED }}>
              Add any of these to a wash while booking.
            </p>
            <ul className="mt-3 flex flex-wrap gap-2">
              {addons.map((a) => (
                <li key={a.id} className="rounded-full bg-[#F4F8FF] px-3.5 py-1.5 text-[14px] font-semibold">
                  {titleCase(a.name)} · {INR(a.price)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <Section title="Which Wash Should I Pick?">
        <ul className="divide-y divide-[#E4EBF5] rounded-[18px] border border-[#EDF0F5]">
          {PICKER.map((row) => {
            const s = SERVICES.find((x) => x.slug === row.pick)!;
            return (
              <li key={row.pick} className="flex flex-wrap items-center justify-between gap-2 px-5 py-4">
                <span className="text-[15px]" style={{ color: MUTED }}>
                  {row.need}
                </span>
                <Link to={`/services/${s.slug}`} className="text-[15px] font-bold" style={{ color: BLUE }}>
                  {s.name}
                </Link>
              </li>
            );
          })}
        </ul>
        <p className="mt-5 text-[15px] leading-[1.7]" style={{ color: MUTED }}>
          Washing regularly? See our{" "}
          <Link to="/plans" className="font-semibold underline" style={{ color: BLUE }}>
            monthly car wash plans
          </Link>
          . Not sure we reach your area?{" "}
          <Link to="/doorstep-car-wash-indore" className="font-semibold underline" style={{ color: BLUE }}>
            See where we wash cars in Indore
          </Link>
          .
        </p>
      </Section>

      <Section title="Good To Know" tint>
        <FaqList faqs={faqs} />
      </Section>

      <CtaBand bookHref={bookHref} />
    </SeoPage>
  );
}
